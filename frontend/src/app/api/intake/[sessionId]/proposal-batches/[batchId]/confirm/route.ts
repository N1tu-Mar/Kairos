import { NextResponse } from "next/server";

import { confirmIntakeBatch } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import { isRevisionBody, validResourceId } from "@/lib/intake-route-contracts";

export const dynamic = "force-dynamic";

interface RouteContext {
  params: Promise<{ sessionId: string; batchId: string }>;
}

export async function POST(request: Request, context: RouteContext) {
  const { sessionId, batchId } = await context.params;
  if (!validResourceId(sessionId) || !validResourceId(batchId)) {
    return NextResponse.json({ error: "Invalid proposal batch." }, { status: 400 });
  }
  const body = await request.json().catch(() => null);
  if (!isRevisionBody(body)) {
    return NextResponse.json({ error: "The current revision is required." }, { status: 400 });
  }
  try {
    return NextResponse.json(
      await confirmIntakeBatch(sessionId, batchId, body.expected_revision),
    );
  } catch (error) {
    return errorResponse(
      error,
      "POST /api/intake/[sessionId]/proposal-batches/[batchId]/confirm",
      "Those proposed facts could not be confirmed.",
      { 409: "Those proposals changed. Review the latest captured facts." },
    );
  }
}
