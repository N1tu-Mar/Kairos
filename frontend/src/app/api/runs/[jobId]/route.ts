import { NextResponse } from "next/server";

import { getJobStatus } from "@/lib/api";
import { errorResponse } from "@/lib/errors";

/**
 * Thin proxy over `GET /founders/{id}/jobs/{job_id}` — the poll target.
 *
 * A run takes minutes and no longer holds a connection open for them, so the
 * manual-run control polls this until the job reaches a terminal status. It
 * forwards one request and adds nothing: no caching, no retry, no derived
 * state. `report` is null until the run produces one.
 */

export const dynamic = "force-dynamic";

/**
 * One job's status plus its report once it has one. The poll target for a running job.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;

  try {
    const status = await getJobStatus(decodeURIComponent(jobId));
    return NextResponse.json(status);
  } catch (error) {
    return errorResponse(
      error,
      "GET /api/runs/[jobId]",
      "The run's status could not be read.",
    );
  }
}
