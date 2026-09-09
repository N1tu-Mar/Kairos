import { NextResponse } from "next/server";

import { updateIntakeClaim } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import { isClaimUpdate, validResourceId } from "@/lib/intake-route-contracts";

export const dynamic = "force-dynamic";

interface RouteContext {
  params: Promise<{ sessionId: string; claimId: string }>;
}

export async function PATCH(request: Request, context: RouteContext) {
  const { sessionId, claimId } = await context.params;
  if (!validResourceId(sessionId) || !validResourceId(claimId)) {
    return NextResponse.json({ error: "Invalid memory claim." }, { status: 400 });
  }
  const body = await request.json().catch(() => null);
  if (!isClaimUpdate(body)) {
    return NextResponse.json({ error: "Invalid memory-claim update." }, { status: 400 });
  }
  try {
    return NextResponse.json(await updateIntakeClaim(sessionId, claimId, body));
  } catch (error) {
    return errorResponse(
      error,
      "PATCH /api/intake/[sessionId]/claims/[claimId]",
      "The memory claim could not be updated.",
      { 409: "The interview changed. Review the latest captured facts." },
    );
  }
}
