// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The production hole: the proxy held KAIROS_API_TOKEN and attached it
 * whenever a Supabase session was missing. An unsigned visitor then acted
 * as the founder. These tests pin that supabase mode must not fetch at all
 * without a user access token, and must never send the shared secret.
 */

beforeEach(() => {
  vi.resetModules();
  process.env.KAIROS_API_URL = "http://127.0.0.1:8000";
  process.env.KAIROS_API_TOKEN = "must-not-be-sent";
  process.env.KAIROS_FOUNDER_ID = "founder_demo";
  process.env.KAIROS_AUTH_MODE = "supabase";
  process.env.VERCEL_ENV = "";
});

afterEach(() => {
  vi.doUnmock("@/lib/supabase/server");
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("supabase mode", () => {
  it("does not call the backend when the session is missing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    vi.doMock("@/lib/supabase/server", () => ({
      currentAccessToken: async () => "",
    }));
    const { triggerRun, createOrResumeIntake, ApiError } = await import("@/lib/api");

    await expect(
      triggerRun({
        source: "manual",
        use_demo_catalog: false,
        include_grants_gov: false,
      }),
    ).rejects.toMatchObject({ kind: "unauthorized", status: 401 });
    await expect(createOrResumeIntake()).rejects.toMatchObject({
      kind: "unauthorized",
      status: 401,
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(ApiError).toBeDefined();
  });

  it("sends the user access token rather than KAIROS_API_TOKEN", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ job_id: "job_1" }), { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.doMock("@/lib/supabase/server", () => ({
      currentAccessToken: async () => "user-jwt",
    }));
    const { triggerRun } = await import("@/lib/api");

    await triggerRun({
      source: "manual",
      use_demo_catalog: false,
      include_grants_gov: false,
    });

    const call = fetchMock.mock.calls[0]!;
    const headers = new Headers(call[1]?.headers);
    expect(headers.get("authorization")).toBe("Bearer user-jwt");
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("must-not-be-sent");
  });
});

describe("local_shared mode", () => {
  it("may still attach the shared token on a laptop", async () => {
    process.env.KAIROS_AUTH_MODE = "local_shared";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.doMock("@/lib/supabase/server", () => ({
      currentAccessToken: async () => "",
    }));
    const { getHealth } = await import("@/lib/api");

    await getHealth();

    const call = fetchMock.mock.calls[0]!;
    const headers = new Headers(call[1]?.headers);
    expect(headers.get("authorization")).toBe("Bearer must-not-be-sent");
  });
});
