import { NextResponse } from "next/server";

import { ApiError, getJobStatus, httpStatusFor } from "@/lib/api";

/**
 * Thin proxy over `GET /founders/{id}/jobs/{job_id}` — the poll target.
 *
 * A run takes minutes and no longer holds a connection open for them, so the
 * manual-run control polls this until the job reaches a terminal status. It
 * forwards one request and adds nothing: no caching, no retry, no derived
 * state. `report` is null until the run produces one.
 */

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;

  try {
    const status = await getJobStatus(decodeURIComponent(jobId));
    return NextResponse.json(status);
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json(
        { error: error.userMessage, detail: error.message, kind: error.kind },
        { status: httpStatusFor(error) },
      );
    }
    return NextResponse.json(
      {
        error: "The run's status could not be read.",
        detail: error instanceof Error ? error.message : String(error),
        kind: "unknown",
      },
      { status: 500 },
    );
  }
}
