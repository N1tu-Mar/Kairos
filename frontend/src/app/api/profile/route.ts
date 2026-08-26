import { NextResponse } from "next/server";

import { ApiError, httpStatusFor, putProfile } from "@/lib/api";
import { founderId } from "@/lib/config";
import type { FounderProfile } from "@/lib/types";

/**
 * Thin proxy over `PUT /founders/{id}`.
 *
 * A whole-object replace, never a patch — the backend refuses anything else,
 * because these fields feed the deterministic eligibility filter and a
 * half-applied update is how a founder gets told they are eligible for
 * something they are not. Pydantic on the backend is the validator of
 * record; this route only pins the founder id to the one this single-founder
 * dashboard is configured for.
 */

export const dynamic = "force-dynamic";

export async function PUT(request: Request) {
  let profile: FounderProfile;
  try {
    profile = (await request.json()) as FounderProfile;
  } catch {
    return NextResponse.json(
      { error: "Request body must be JSON." },
      { status: 400 },
    );
  }

  if (profile.founder_id !== founderId()) {
    return NextResponse.json(
      { error: "founder_id does not match the founder this dashboard serves." },
      { status: 400 },
    );
  }

  try {
    // The backend redacts on write and returns what it stored. That stored
    // object is what renders, so the founder sees the truth, not the request.
    const stored = await putProfile(profile);
    return NextResponse.json(stored);
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json(
        { error: error.userMessage, detail: error.message, kind: error.kind },
        { status: httpStatusFor(error) },
      );
    }
    return NextResponse.json(
      {
        error: "The profile could not be saved.",
        detail: error instanceof Error ? error.message : String(error),
        kind: "unknown",
      },
      { status: 500 },
    );
  }
}
