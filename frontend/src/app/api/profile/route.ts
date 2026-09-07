import { NextResponse } from "next/server";

import { currentFounderId, putProfile } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import type { FounderProfile } from "@/lib/types";

/**
 * Thin proxy over `PUT /founders/{id}`.
 *
 * A whole-object replace, never a patch — the backend refuses anything else,
 * because these fields feed the deterministic eligibility filter and a
 * half-applied update is how a founder gets told they are eligible for
 * something they are not. Pydantic on the backend is the validator of
 * record; this route only pins the founder id to the one this session owns.
 *
 * *Which* founder that is comes from `currentFounderId`, never from
 * `KAIROS_FOUNDER_ID`. That variable names one founder for the whole
 * deployment — right on a laptop, and wrong the moment anyone can sign up. A
 * new account owns an auto-provisioned founder, so every save was refused as
 * a mismatch and the profile page was unreachable for exactly the people who
 * had just made an account.
 */

export const dynamic = "force-dynamic";

/**
 * Replace the profile. 400 for a non-JSON body or a mismatched `founder_id`.
 *
 * Returns what the backend *stored*, not what was sent — the two differ
 * wherever redaction applied, and the stored version is what every other
 * view will show.
 */
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

  try {
    // Resolved before the body is trusted. A session that owns no founder
    // raises here, which is a 403 the reader can act on rather than a write
    // attempted against whatever id the body happened to name.
    if (profile.founder_id !== (await currentFounderId())) {
      return NextResponse.json(
        {
          error:
            "founder_id does not match the founder this session owns.",
        },
        { status: 400 },
      );
    }

    // The backend redacts on write and returns what it stored. That stored
    // object is what renders, so the founder sees the truth, not the request.
    const stored = await putProfile(profile);
    return NextResponse.json(stored);
  } catch (error) {
    return errorResponse(
      error,
      "PUT /api/profile",
      "The profile could not be saved.",
    );
  }
}
