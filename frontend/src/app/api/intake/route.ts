import { NextResponse } from "next/server";

import { createOrResumeIntake } from "@/lib/api";
import { errorResponse } from "@/lib/errors";

export const dynamic = "force-dynamic";

/** Same-origin proxy; backend location and AWS configuration stay server-only. */
export async function POST() {
  try {
    return NextResponse.json(await createOrResumeIntake());
  } catch (error) {
    return errorResponse(
      error,
      "POST /api/intake",
      "The founder interview could not be loaded.",
    );
  }
}
