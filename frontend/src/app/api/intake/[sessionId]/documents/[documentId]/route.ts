import { NextResponse } from "next/server";

import { removeIntakeDocument } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import { validResourceId } from "@/lib/intake-route-contracts";

export const dynamic = "force-dynamic";

interface RouteContext {
  params: Promise<{ sessionId: string; documentId: string }>;
}

export async function DELETE(_request: Request, context: RouteContext) {
  const { sessionId, documentId } = await context.params;
  if (!validResourceId(sessionId) || !validResourceId(documentId)) {
    return NextResponse.json({ error: "Invalid intake document." }, { status: 400 });
  }
  try {
    await removeIntakeDocument(sessionId, documentId);
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    return errorResponse(
      error,
      "DELETE /api/intake/[sessionId]/documents/[documentId]",
      "The document could not be removed.",
    );
  }
}
