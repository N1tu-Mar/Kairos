import { NextResponse } from "next/server";

import { ApiError, getHealth } from "@/lib/api";

/** Backend liveness, proxied. Used by the manual run control before it posts. */
export const dynamic = "force-dynamic";

/**
 * 502 with a readable message when the backend cannot be reached, rather than
 * throwing — the caller uses this to decide whether posting a run is worth
 * trying, so "down" has to be an answer it can render.
 */
export async function GET() {
  try {
    return NextResponse.json(await getHealth());
  } catch (error) {
    const message =
      error instanceof ApiError ? error.userMessage : "Backend unreachable.";
    return NextResponse.json({ status: "unreachable", error: message }, { status: 502 });
  }
}
