// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The lock on the front door, and proof it is actually locked.
 *
 * The hole this closes was structural rather than a missing feature. The
 * proxy routes in `src/app/api` hold the backend's credential and attach it to
 * whatever they are asked to do, so before this middleware existed an
 * unauthenticated visitor could read every draft and trigger runs that cost
 * money. FastAPI's authorization never saw an anonymous request — the proxy
 * made every request on the founder's behalf.
 *
 * So these tests are about one question: does an unauthenticated request reach
 * anything. The answer has to be no for pages and for proxy routes alike,
 * because the proxy routes are the ones that spend money.
 */

const SUPABASE_URL = "https://abcdefghijklm.supabase.co";
const ANON_KEY = "anon-key-for-tests";

/** Swap Supabase's user lookup for a fixed answer. */
function withUser(user: unknown) {
  vi.doMock("@supabase/ssr", () => ({
    createServerClient: () => ({
      auth: { getUser: async () => ({ data: { user }, error: null }) },
    }),
  }));
}

async function request(path: string) {
  // Imported lazily so each test's mock is the one in force.
  const { NextRequest } = await import("next/server");
  return new NextRequest(new URL(path, "https://kairos.example"));
}

beforeEach(() => {
  vi.resetModules();
  process.env.NEXT_PUBLIC_SUPABASE_URL = SUPABASE_URL;
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = ANON_KEY;
});

afterEach(() => {
  vi.doUnmock("@supabase/ssr");
  vi.unstubAllEnvs();
});

describe("an anonymous visitor", () => {
  it.each([
    "/",
    "/inbox",
    "/runs",
    "/drafts",
    "/drafts/draft_123",
    "/profile",
  ])("is redirected away from %s", async (path) => {
    withUser(null);
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request(path));

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toContain("/login");
  });

  it("cannot reach the proxy route that starts a run", async () => {
    // The one that costs money on every call.
    withUser(null);
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request("/api/runs"));

    expect(response.headers.get("location")).toContain("/login");
  });

  it.each(["/api/profile", "/api/inbox/run_1:opp_1", "/api/runs/job_1"])(
    "cannot reach %s",
    async (path) => {
      withUser(null);
      const { middleware } = await import("@/middleware");

      const response = await middleware(await request(path));

      expect(response.headers.get("location")).toContain("/login");
    },
  );

  it("keeps where they were going, so signing in does not lose the page", async () => {
    withUser(null);
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request("/drafts/draft_123"));

    const location = new URL(response.headers.get("location")!);
    expect(location.searchParams.get("next")).toBe("/drafts/draft_123");
  });

  it("can still reach the login page, or signing in is impossible", async () => {
    withUser(null);
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request("/login"));

    expect(response.headers.get("location")).toBeNull();
  });
});

describe("a signed-in visitor", () => {
  it("is let through to the dashboard", async () => {
    withUser({ id: "a3f1c9e2-7b44-4d18-9f2a-1c8e5b0d6a37" });
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request("/inbox"));

    expect(response.headers.get("location")).toBeNull();
  });

  it("is let through to the proxy routes", async () => {
    withUser({ id: "a3f1c9e2-7b44-4d18-9f2a-1c8e5b0d6a37" });
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request("/api/runs"));

    expect(response.headers.get("location")).toBeNull();
  });
});

describe("when Supabase is not configured", () => {
  it("steps aside, leaving the documented local single-founder mode", async () => {
    // Not a hole being reintroduced: this is the laptop posture, the same one
    // KAIROS_ALLOW_OPEN_API is for on the backend. It is reachable only by
    // leaving both variables unset *and* not being a Vercel deploy.
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "");
    vi.stubEnv("KAIROS_AUTH_MODE", "local_shared");
    vi.stubEnv("VERCEL_ENV", "");
    withUser(null);
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request("/inbox"));

    expect(response.headers.get("location")).toBeNull();
    expect(response.status).not.toBe(503);
  });

  it("returns a generic 503 in supabase mode when the public variables are missing", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "");
    vi.stubEnv("KAIROS_AUTH_MODE", "supabase");
    withUser(null);
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request("/inbox"));

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail: "service unavailable" });
  });

  it("does not honour local_shared on a Vercel production deploy", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "");
    vi.stubEnv("KAIROS_AUTH_MODE", "local_shared");
    vi.stubEnv("VERCEL_ENV", "production");
    withUser(null);
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request("/api/runs"));

    expect(response.status).toBe(503);
  });
});

describe("the content security policy", () => {
  it("gives Next scripts a per-request nonce without allowing arbitrary inline scripts", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "");
    withUser(null);
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request("/"));
    const policy = response.headers.get("content-security-policy");
    const nonce = policy?.match(/'nonce-([^']+)'/)?.[1];

    expect(policy).toMatch(/script-src[^;]*'nonce-[^']+'/);
    expect(policy).toContain("'strict-dynamic'");
    expect(policy).not.toMatch(/script-src[^;]*'unsafe-inline'/);
    // Response CSP protects the browser. These forwarded request headers are
    // equally important: Next reads them before rendering and stamps this
    // nonce onto the inline scripts that replace loading.tsx.
    expect(
      response.headers.get("x-middleware-request-content-security-policy"),
    ).toBe(policy);
    expect(response.headers.get("x-middleware-request-x-nonce")).toBe(nonce);
  });

  it("uses a different nonce for every request", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "");
    withUser(null);
    const { middleware } = await import("@/middleware");

    const first = await middleware(await request("/"));
    const second = await middleware(await request("/"));

    expect(first.headers.get("content-security-policy")).not.toBe(
      second.headers.get("content-security-policy"),
    );
  });
});

describe("the redirect target", () => {
  it("is always a path on this origin", async () => {
    // `next` is echoed back into the login page. A crafted link must not be
    // able to turn signing in into a bounce off-site carrying this origin's
    // trust.
    withUser(null);
    const { middleware } = await import("@/middleware");

    const response = await middleware(await request("/inbox"));

    const location = new URL(response.headers.get("location")!);
    expect(location.origin).toBe("https://kairos.example");
  });
});
