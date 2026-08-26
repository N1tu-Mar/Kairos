import { NextResponse } from "next/server";

import { ApiError, httpStatusFor, triggerRun } from "@/lib/api";
import type { RunTrigger } from "@/lib/types";

/**
 * Thin backend-for-frontend proxy over `POST /founders/{id}/runs`.
 *
 * It exists so the browser never needs the FastAPI address, and it does
 * nothing else: no queueing, no scheduling, no retry, no state. One click,
 * one forwarded request, one RunReport back.
 *
 * Scheduled invocation is deliberately absent. See `incomplete.md`.
 */

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  let trigger: RunTrigger;
  try {
    const body = (await request.json()) as Partial<RunTrigger>;
    trigger = {
      use_demo_catalog: body.use_demo_catalog === true,
      include_grants_gov: body.include_grants_gov !== false,
    };
  } catch {
    return NextResponse.json(
      { error: "Request body must be JSON." },
      { status: 400 },
    );
  }

  try {
    const report = await triggerRun(trigger);
    return NextResponse.json(report);
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json(
        { error: error.userMessage, detail: error.message, kind: error.kind },
        { status: httpStatusFor(error) },
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
