// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The dashboard must not describe the deployment to whoever is looking at it.
 *
 * `agent/sanitize.py::safe_detail` has enforced this on the Python side for a
 * while, and `tests/test_leak_surface.py` keeps it enforced. The proxy routes
 * in `src/app/api` are the boundary that skipped it: each one caught its
 * error and returned `detail: error.message` straight to the browser, and
 * `ApiError.userMessage` interpolated the backend's own base URL into the
 * unreachable case. A stranger who could reach the dashboard could ask it
 * where its backend lives and be told.
 *
 * Two audiences, two messages: the operator gets the detail in the server
 * log, the browser gets a stable sentence and a request id to quote. These
 * tests hold that line for every route and every failure mode, because the
 * next route added is the one that forgets.
 */

const API_BASE = "http://kairos-internal.svc.cluster.local:8000";

/** Everything a response body must never contain, however it was phrased. */
const FORBIDDEN = [
  API_BASE,
  "kairos-internal.svc.cluster.local",
  "cluster.local",
  ":8000",
  "ECONNREFUSED",
  "127.0.0.1",
];

beforeEach(() => {
  vi.resetModules();
  process.env.KAIROS_API_URL = API_BASE;
  process.env.KAIROS_FOUNDER_ID = "founder_demo";
  process.env.KAIROS_API_TOKEN = "test-token-value";
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** Force the backend call to fail the way a real outage does. */
function backendUnreachable() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new TypeError(
        `fetch failed: connect ECONNREFUSED 127.0.0.1:8000 (${API_BASE})`,
      );
    }),
  );
}

/** The backend answers, but with a status the route has to forward. */
function backendReturns(status: number) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: `upstream said ${status}` }), {
          status,
          headers: { "content-type": "application/json" },
        }),
    ),
  );
}

function assertNoLeak(body: string) {
  for (const secret of FORBIDDEN) {
    expect(body).not.toContain(secret);
  }
}

async function bodyOf(response: Response): Promise<string> {
  return await response.text();
}

describe("proxy routes never name the backend", () => {
  it("PUT /api/profile keeps the host out of an unreachable-backend error", async () => {
    backendUnreachable();
    const { PUT } = await import("@/app/api/profile/route");

    const response = await PUT(
      new Request("http://localhost:3000/api/profile", {
        method: "PUT",
        body: JSON.stringify({ founder_id: "founder_demo" }),
      }),
    );

    assertNoLeak(await bodyOf(response));
  });

  it("PATCH /api/inbox/[itemId] keeps the host out of an unreachable-backend error", async () => {
    backendUnreachable();
    const { PATCH } = await import("@/app/api/inbox/[itemId]/route");

    const response = await PATCH(
      new Request("http://localhost:3000/api/inbox/run_1:opp_1", {
        method: "PATCH",
        body: JSON.stringify({ state: "dismissed" }),
      }),
      { params: Promise.resolve({ itemId: "run_1:opp_1" }) },
    );

    assertNoLeak(await bodyOf(response));
  });

  it("POST /api/runs keeps the host out of an unreachable-backend error", async () => {
    backendUnreachable();
    const { POST } = await import("@/app/api/runs/route");

    const response = await POST(
      new Request("http://localhost:3000/api/runs", {
        method: "POST",
        body: JSON.stringify({}),
      }),
    );

    assertNoLeak(await bodyOf(response));
  });

  it("GET /api/runs/[jobId] keeps the host out of an unreachable-backend error", async () => {
    backendUnreachable();
    const { GET } = await import("@/app/api/runs/[jobId]/route");

    const response = await GET(
      new Request("http://localhost:3000/api/runs/job_1"),
      { params: Promise.resolve({ jobId: "job_1" }) },
    );

    assertNoLeak(await bodyOf(response));
  });

  it("keeps the host out of an upstream HTTP error too", async () => {
    backendReturns(500);
    const { POST } = await import("@/app/api/runs/route");

    const response = await POST(
      new Request("http://localhost:3000/api/runs", {
        method: "POST",
        body: JSON.stringify({}),
      }),
    );

    assertNoLeak(await bodyOf(response));
  });
});

describe("what the browser gets instead", () => {
  it("carries a request id the operator can grep for", async () => {
    backendUnreachable();
    const { POST } = await import("@/app/api/runs/route");

    const response = await POST(
      new Request("http://localhost:3000/api/runs", {
        method: "POST",
        body: JSON.stringify({}),
      }),
    );
    const body = (await response.json()) as { requestId?: string };

    expect(body.requestId).toMatch(/^req_[0-9a-f]{12}$/);
  });

  it("keeps `kind`, which the UI branches on and which carries no data", async () => {
    backendUnreachable();
    const { POST } = await import("@/app/api/runs/route");

    const response = await POST(
      new Request("http://localhost:3000/api/runs", {
        method: "POST",
        body: JSON.stringify({}),
      }),
    );
    const body = (await response.json()) as { kind?: string };

    expect(body.kind).toBe("unreachable");
  });

  it("drops `detail` entirely rather than trying to scrub it", async () => {
    backendUnreachable();
    const { POST } = await import("@/app/api/runs/route");

    const response = await POST(
      new Request("http://localhost:3000/api/runs", {
        method: "POST",
        body: JSON.stringify({}),
      }),
    );
    const body = (await response.json()) as Record<string, unknown>;

    expect(body).not.toHaveProperty("detail");
  });

  it("still says something a human can act on", async () => {
    backendUnreachable();
    const { POST } = await import("@/app/api/runs/route");

    const response = await POST(
      new Request("http://localhost:3000/api/runs", {
        method: "POST",
        body: JSON.stringify({}),
      }),
    );
    const body = (await response.json()) as { error?: string };

    expect(body.error).toBeTruthy();
    expect(body.error).toMatch(/could not reach/i);
  });
});

describe("the operator's half", () => {
  it("logs the full detail against the same request id", async () => {
    backendUnreachable();
    const logged: unknown[][] = [];
    vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
      logged.push(args);
    });
    const { POST } = await import("@/app/api/runs/route");

    const response = await POST(
      new Request("http://localhost:3000/api/runs", {
        method: "POST",
        body: JSON.stringify({}),
      }),
    );
    const { requestId } = (await response.json()) as { requestId: string };

    const line = JSON.stringify(logged);
    expect(line).toContain(requestId);
    expect(line).toContain("ECONNREFUSED");
  });
});
