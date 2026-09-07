// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Which founder this session is looking at.
 *
 * `KAIROS_FOUNDER_ID` names one founder for the whole deployment. That is
 * right for a laptop and wrong the moment two people can sign in: both are
 * shown the same inbox, each believing it is theirs. It is the tenancy
 * equivalent of the shared-token hole `api-auth.test.ts` closed — the
 * dashboard acting for a founder nobody proved they own.
 *
 * In supabase mode the id comes from `/me`, which reports what the bearer
 * token's own membership rows say. The environment variable survives only as
 * the laptop-mode answer, where there is no session to ask about.
 */

function withSession(token = "user-token") {
  vi.doMock("@/lib/supabase/server", () => ({
    currentAccessToken: async () => token,
  }));
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

beforeEach(() => {
  vi.resetModules();
  process.env.KAIROS_API_URL = "http://127.0.0.1:8000";
  process.env.KAIROS_API_TOKEN = "";
  process.env.KAIROS_FOUNDER_ID = "founder_from_env";
  process.env.KAIROS_AUTH_MODE = "supabase";
  process.env.VERCEL_ENV = "";
});

afterEach(() => {
  vi.doUnmock("@/lib/supabase/server");
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("supabase mode", () => {
  it("takes the founder id from /me, not the environment", async () => {
    withSession();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          subject: "user-abc",
          founder_id: "founder_session",
          founder_ids: ["founder_session"],
          can_write: true,
          method: "supabase_jwt",
        }),
      ),
    );
    const { currentFounderId } = await import("@/lib/api");

    await expect(currentFounderId()).resolves.toBe("founder_session");
  });

  it("asks the backend rather than trusting a cookie", async () => {
    withSession();
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        subject: "user-abc",
        founder_id: "founder_session",
        founder_ids: ["founder_session"],
        can_write: true,
        method: "supabase_jwt",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { currentFounderId } = await import("@/lib/api");

    await currentFounderId();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/me",
      expect.objectContaining({
        headers: expect.objectContaining({ authorization: "Bearer user-token" }),
      }),
    );
  });

  it("refuses when the session owns no founder", async () => {
    withSession();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          subject: "user-abc",
          founder_id: null,
          founder_ids: [],
          can_write: true,
          method: "supabase_jwt",
        }),
      ),
    );
    const { currentFounderId } = await import("@/lib/api");

    // Signed in, granted nothing. Falling back to KAIROS_FOUNDER_ID here
    // would hand a stranger the demo founder's inbox — the exact failure
    // this module exists to prevent.
    await expect(currentFounderId()).rejects.toMatchObject({
      kind: "no_founder",
    });
  });

  it("does not fall back to the environment when /me is unreachable", async () => {
    withSession();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("connection refused");
      }),
    );
    const { currentFounderId } = await import("@/lib/api");

    await expect(currentFounderId()).rejects.toMatchObject({
      kind: "unreachable",
    });
  });

  it("does not call /me without a session", async () => {
    vi.doMock("@/lib/supabase/server", () => ({
      currentAccessToken: async () => "",
    }));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { currentFounderId } = await import("@/lib/api");

    await expect(currentFounderId()).rejects.toMatchObject({
      kind: "unauthorized",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("local_shared mode", () => {
  beforeEach(() => {
    process.env.KAIROS_AUTH_MODE = "local_shared";
  });

  it("keeps using KAIROS_FOUNDER_ID", async () => {
    vi.doMock("@/lib/supabase/server", () => ({
      currentAccessToken: async () => "",
    }));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { currentFounderId } = await import("@/lib/api");

    await expect(currentFounderId()).resolves.toBe("founder_from_env");
  });

  it("does not call /me at all", async () => {
    vi.doMock("@/lib/supabase/server", () => ({
      currentAccessToken: async () => "",
    }));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { currentFounderId } = await import("@/lib/api");

    await currentFounderId();

    // There is no session to ask about, and asking would 401 on a laptop
    // running with no credential at all.
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("reads default to the session's founder", () => {
  it("getProfile with no argument fetches the founder /me named", async () => {
    withSession();
    const fetchMock = vi.fn(async (url: string) =>
      url.endsWith("/me")
        ? jsonResponse({
            subject: "user-abc",
            founder_id: "founder_session",
            founder_ids: ["founder_session"],
            can_write: true,
            method: "supabase_jwt",
          })
        : jsonResponse({ founder_id: "founder_session" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { getProfile } = await import("@/lib/api");

    await getProfile();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/founders/founder_session",
      expect.anything(),
    );
  });

  it("an explicit id is still honoured", async () => {
    withSession();
    const fetchMock = vi.fn(async () =>
      jsonResponse({ founder_id: "founder_explicit" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { getProfile } = await import("@/lib/api");

    await getProfile("founder_explicit");

    // No /me call: the caller already knows which founder it means. The
    // backend still authorizes it, so this cannot reach another tenant.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/founders/founder_explicit",
      expect.anything(),
    );
  });
});
