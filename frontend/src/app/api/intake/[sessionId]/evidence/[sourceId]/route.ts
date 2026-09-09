import { NextResponse } from "next/server";

import { getIntakeEvidence } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import { validResourceId } from "@/lib/intake-route-contracts";

export const dynamic = "force-dynamic";

interface RouteContext {
  params: Promise<{ sessionId: string; sourceId: string }>;
}

export async function GET(_request: Request, context: RouteContext) {
  const { sessionId, sourceId } = await context.params;
  if (!validResourceId(sessionId) || !validResourceId(sourceId)) {
    return NextResponse.json({ error: "Invalid evidence reference." }, { status: 400 });
  }
  try {
    return NextResponse.json(await getIntakeEvidence(sessionId, sourceId));
  } catch (error) {
    return errorResponse(
      error,
      "GET /api/intake/[sessionId]/evidence/[sourceId]",
      "The supporting evidence could not be loaded.",
    );
  }
}
