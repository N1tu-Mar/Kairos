// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Where an OAuth provider sends someone back to.
 *
 * `middleware.ts` has listed `/auth/callback` as a public path since the gate
 * was built, and the route did not exist — so Google and GitHub sign-in could
 * not complete at all. This is that route.
 *
 * It is the one place in the app that turns a URL parameter into a session,
 * which makes it the one place where a crafted link is worth writing. Two
 * things are therefore load-bearing and tested here rather than reasoned
 * about: the code is exchanged server-side, and `next` can only ever be a
 * path on this origin.
 */

const EXCHANGE = vi.hoisted(() => vi.fn());

vi.mock("@/lib/supabase/server", () => ({
  supabaseServerClient: async () => ({
    auth: { exchangeCodeForSession: EXCHANGE },
  }),
}));

async function callback(query: string) {
  const { GET } = await import("@/app/auth/callback/route");
  return GET(new Request(`https://kairos.example/auth/callback${query}`));
}

beforeEach(() => {
  vi.resetModules();
  EXCHANGE.mockReset();
  EXCHANGE.mockResolvedValue({ error: null });
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("a completed sign-in", () => {
  it("exchanges the code for a session", async () => {
    await callback("?code=auth-code-123");

    expect(EXCHANGE).toHaveBeenCalledWith("auth-code-123");
  });

  it("lands on the dashboard root by default", async () => {
    const response = await callback("?code=auth-code-123");

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("https://kairos.example/");
  });

  it("returns the visitor to where they were headed", async () => {
    const response = await callback("?code=auth-code-123&next=%2Finbox");

    expect(response.headers.get("location")).toBe(
      "https://kairos.example/inbox",
    );
  });
});

describe("the redirect target", () => {
  it.each([
    ["an absolute url", "https%3A%2F%2Fevil.example%2Fsteal"],
    ["a protocol-relative url", "%2F%2Fevil.example%2Fsteal"],
    ["a scheme with no slashes", "javascript%3Aalert(1)"],
    ["a backslash-prefixed path", "%5C%5Cevil.example"],
  ])("refuses %s", async (_name, next) => {
    const response = await callback(`?code=auth-code-123&next=${next}`);

    // A sign-in link that bounces somebody to another site carries the trust
    // of this one. `login-form.tsx` guards the same parameter the same way.
    expect(response.headers.get("location")).toBe("https://kairos.example/");
  });

  it("keeps a nested path on this origin", async () => {
    const response = await callback(
      "?code=auth-code-123&next=%2Fdrafts%2Fdraft_123",
    );

    expect(response.headers.get("location")).toBe(
      "https://kairos.example/drafts/draft_123",
    );
  });
});

describe("a sign-in that did not complete", () => {
  it("sends a failed exchange back to the login page", async () => {
    EXCHANGE.mockResolvedValue({ error: { message: "bad code" } });

    const response = await callback("?code=stale-code");

    expect(response.headers.get("location")).toContain("/login");
  });

  it("does not echo the provider's message into the URL", async () => {
    EXCHANGE.mockResolvedValue({ error: { message: "user-not-allowed" } });

    const response = await callback("?code=stale-code");

    // The parameter is a fixed token the login page maps to its own copy.
    // Reflecting provider text puts attacker-influenced content in a URL a
    // person is about to read and trust.
    expect(response.headers.get("location")).not.toContain("user-not-allowed");
  });

  it("handles a provider that returned an error instead of a code", async () => {
    const response = await callback(
      "?error=access_denied&error_description=User+denied",
    );

    expect(response.headers.get("location")).toContain("/login");
    expect(EXCHANGE).not.toHaveBeenCalled();
  });

  it("refuses a request carrying no code at all", async () => {
    const response = await callback("");

    expect(response.headers.get("location")).toContain("/login");
    expect(EXCHANGE).not.toHaveBeenCalled();
  });

  it("survives an exchange that throws", async () => {
    EXCHANGE.mockRejectedValue(new Error("network down"));

    const response = await callback("?code=auth-code-123");

    // Supabase being unreachable must not surface as an unhandled 500 on the
    // one route a person hits mid-sign-in.
    expect(response.headers.get("location")).toContain("/login");
  });
});
