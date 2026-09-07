// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Which founder the profile proxy will write.
 *
 * The route pins the body's `founder_id` to the founder this session owns,
 * so a crafted body cannot replace somebody else's profile. The bug this
 * covers is which founder "this session" means: reading `KAIROS_FOUNDER_ID`
 * is the laptop answer, and in supabase mode it names the demo founder while
 * the signed-in person owns an auto-provisioned one. Every save then failed
 * with a message about a mismatch the founder could not act on — the profile
 * page was unreachable for exactly the people who had just made an account.
 */

const PROFILE = { founder_id: "founder_session", institution: "State" };

beforeEach(() => {
  vi.resetModules();
  process.env.KAIROS_FOUNDER_ID = "founder_from_env";
});

afterEach(() => {
  vi.resetModules();
  vi.restoreAllMocks();
});

function put(body: unknown) {
  return new Request("http://localhost:3000/api/profile", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

describe("profile proxy", () => {
  it("writes the founder this session owns, not the configured one", async () => {
    const putProfile = vi.fn().mockResolvedValue(PROFILE);
    vi.doMock("@/lib/api", () => ({
      putProfile,
      currentFounderId: async () => "founder_session",
    }));
    const { PUT } = await import("@/app/api/profile/route");

    const response = await PUT(put(PROFILE));

    expect(response.status).toBe(200);
    expect(putProfile).toHaveBeenCalledWith(PROFILE);
  });

  it("refuses a body naming a founder this session does not own", async () => {
    const putProfile = vi.fn();
    vi.doMock("@/lib/api", () => ({
      putProfile,
      currentFounderId: async () => "founder_session",
    }));
    const { PUT } = await import("@/app/api/profile/route");

    const response = await PUT(put({ ...PROFILE, founder_id: "founder_other" }));

    expect(response.status).toBe(400);
    expect(putProfile).not.toHaveBeenCalled();
  });

  it("reports a session that owns no founder rather than writing one", async () => {
    const putProfile = vi.fn();
    const actual =
      await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    vi.doMock("@/lib/api", () => ({
      ...actual,
      putProfile,
      currentFounderId: async () => {
        throw new actual.ApiError(
          "no_founder",
          "This session owns no founder",
          "/me",
          403,
        );
      },
    }));
    const { PUT } = await import("@/app/api/profile/route");

    const response = await PUT(put(PROFILE));

    expect(response.status).toBe(403);
    expect(putProfile).not.toHaveBeenCalled();
  });
});
