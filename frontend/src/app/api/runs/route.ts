import { NextResponse } from "next/server";

import { ApiError, httpStatusFor, triggerRun } from "@/lib/api";
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
    if (error instanceof ApiError) {
      const status = httpStatusFor(error);
      return NextResponse.json(
        {
          error:
            status === 409
              ? "A run is already in progress for this founder."
              : error.userMessage,
          detail: error.message,
          kind: error.kind,
        },
        { status },
      );
    }
    return NextResponse.json(
      {
        error: "The run could not be started.",
        detail: error instanceof Error ? error.message : String(error),
        kind: "unknown",
      },
      { status: 500 },
    );
  }
}
