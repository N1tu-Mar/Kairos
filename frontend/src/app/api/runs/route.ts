import { NextResponse } from "next/server";

import { triggerRun } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import type { RunTrigger } from "@/lib/types";

/**
 * Thin backend-for-frontend proxy over `POST /founders/{id}/runs`.
 *
 * It exists so the browser never needs the FastAPI address, and it does
 * nothing else: no queueing, no scheduling, no retry, no state. The backend
 * owns all of that now — this forwards one request and returns the job it
 * created, immediately. The run itself happens on the backend and the client
 * polls `/api/runs/{jobId}`.
 *
 * 409 (another run already holds the lease) and 200 (this idempotency key
 * already landed) are forwarded as-is rather than flattened into an error:
 * both are real answers about a real run, and the UI says different things
 * about them.
 */

export const dynamic = "force-dynamic";

/**
 * Start a run and return the created job with 202. Does not wait for the run.
 *
 * The trigger is rebuilt field by field rather than forwarded, so an
 * unexpected key in the body cannot reach the backend, and `source` is
 * always `"manual"` — a client cannot claim to be the scheduler.
 */
export async function POST(request: Request) {
  let trigger: RunTrigger;
  try {
    const body = (await request.json()) as Partial<RunTrigger>;
    trigger = {
      use_demo_catalog: body.use_demo_catalog === true,
      include_grants_gov: body.include_grants_gov !== false,
      source: "manual",
    };
    // A key the client generated per click. Without it, a double-submit or a
    // retried fetch is a second run; with it, it resolves to the first.
    if (typeof body.idempotency_key === "string" && body.idempotency_key) {
      trigger.idempotency_key = body.idempotency_key.slice(0, 200);
    }
  } catch {
    return NextResponse.json(
      { error: "Request body must be JSON." },
      { status: 400 },
    );
  }

  try {
    const job = await triggerRun(trigger);
    return NextResponse.json(job, { status: 202 });
  } catch (error) {
    return errorResponse(
      error,
      "POST /api/runs",
      "The run could not be started.",
      // A 409 is the lease held by a run already going, which is worth saying
      // plainly to someone who pressed the button twice.
      { 409: "A run is already in progress for this founder." },
    );
  }
}
