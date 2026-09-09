import { NextResponse } from "next/server";

import { abandonIntake } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import { isRevisionBody, validResourceId } from "@/lib/intake-route-contracts";

export const dynamic = "force-dynamic";

interface RouteContext {
  params: Promise<{ sessionId: string }>;
}

export async function DELETE(request: Request, context: RouteContext) {
  const { sessionId } = await context.params;
  if (!validResourceId(sessionId)) {
    return NextResponse.json({ error: "Invalid intake session." }, { status: 400 });
  }
  const body = await request.json().catch(() => null);
  if (!isRevisionBody(body)) {
    return NextResponse.json({ error: "The current revision is required." }, { status: 400 });
  }
  try {
    return NextResponse.json(await abandonIntake(sessionId, body.expected_revision));
  } catch (error) {
    return errorResponse(
      error,
      "DELETE /api/intake/[sessionId]",
      "The interview could not be abandoned.",
    );
  }
}
