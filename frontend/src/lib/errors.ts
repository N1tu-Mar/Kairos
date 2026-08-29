import { NextResponse } from "next/server";

import { ApiError, httpStatusFor } from "@/lib/api";

/**
 * The one place a proxy route turns a failure into a response.
 *
 * `agent/sanitize.py::safe_detail` is the same idea on the Python side, and
 * `tests/test_leak_surface.py` keeps it honest there. This is the boundary
 * that skipped it: each route caught its own error and returned
 * `detail: error.message` — a raw fetch failure carrying the backend's
 * hostname, its port, and `ECONNREFUSED 127.0.0.1` — straight to a browser
 * that had not authenticated for anything.
 *
 * **Two audiences, two messages.** The operator needs the detail and gets it
 * in the server log. The browser needs to know something failed and what to
 * quote when asking about it, and gets that instead. Neither is degraded:
 * the detail is not scrubbed and shortened, it is simply sent somewhere the
 * stranger is not.
 *
 * Routes call `errorResponse` rather than shaping this themselves, because
 * four hand-written copies of a rule are four chances to forget it — which is
 * how the `detail` leak got into all four in the first place.
 */

/** What a proxy route sends the browser when something failed. */
export interface ProxyErrorBody {
  /** One sentence a human can act on. Never names infrastructure. */
  error: string;
  /**
   * Closed set, so the UI can branch. Carries no data — it is the same five
   * values whatever the deployment looks like.
   */
  kind: ApiError["kind"] | "unknown";
  /** What the operator greps for. Matches the id the log line carries. */
  requestId: string;
}

/**
 * A short correlation id, generated per failure.
 *
 * `crypto.randomUUID` trimmed to 12 hex characters: long enough not to
 * collide within a log retention window, short enough that someone can read
 * it off a screen and paste it into a support message.
 *
 * Deliberately generated here rather than taken from a client-supplied
 * header. A propagated trace id — client through proxy into FastAPI's own
 * logs — is the better end state and is easy to move to later; it needs a
 * trusted client and structured logging on both sides, and neither exists
 * yet. Generating it here closes the leak today without pretending to a
 * tracing story we do not have.
 */
export function newRequestId(): string {
  return `req_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
}

/**
 * Log the operator's half, and return the browser's half.
 *
 * `context` names the route, so a log line says which call failed without the
 * reader reconstructing it from a stack. Everything sensitive — the message,
 * the path, the upstream status — goes here and stops here.
 */
export function errorResponse(
  error: unknown,
  context: string,
  fallbackMessage: string,
  /**
   * Replace the sentence for particular upstream statuses.
   *
   * One caller needs it: a 409 from the run trigger means a run is already in
   * progress, which is a normal thing to tell someone who pressed the button
   * twice, not "the API returned 409". Keyed by status so an override cannot
   * accidentally widen to every failure of that route.
   */
  messageByStatus: Record<number, string> = {},
): NextResponse<ProxyErrorBody> {
  const requestId = newRequestId();

  if (error instanceof ApiError) {
    // `error.path` is a backend route and `error.message` is whatever the
    // network or the upstream said. Both are useful; neither is public.
    console.error(
      JSON.stringify({
        requestId,
        context,
        kind: error.kind,
        status: error.status,
        path: error.path,
        message: error.message,
      }),
    );
    const status = httpStatusFor(error);
    return NextResponse.json(
      {
        error: messageByStatus[status] ?? error.userMessage,
        kind: error.kind,
        requestId,
      },
      { status },
    );
  }

  console.error(
    JSON.stringify({
      requestId,
      context,
      kind: "unknown",
      message: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : undefined,
    }),
  );
  return NextResponse.json(
    { error: fallbackMessage, kind: "unknown", requestId },
    { status: 500 },
  );
}
