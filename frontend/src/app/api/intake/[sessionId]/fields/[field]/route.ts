import { NextResponse } from "next/server";

import { updateIntakeField } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import { isFactUpdate, isIntakeField, validResourceId } from "@/lib/intake-route-contracts";

export const dynamic = "force-dynamic";

interface RouteContext {
  params: Promise<{ sessionId: string; field: string }>;
}

export async function PATCH(request: Request, context: RouteContext) {
  const { sessionId, field } = await context.params;
  if (!validResourceId(sessionId) || !isIntakeField(field)) {
    return NextResponse.json({ error: "Invalid founder fact." }, { status: 400 });
  }
  const body = await request.json().catch(() => null);
  if (!isFactUpdate(body)) {
    return NextResponse.json({ error: "Invalid founder-fact update." }, { status: 400 });
  }
  try {
    return NextResponse.json(await updateIntakeField(sessionId, field, body));
  } catch (error) {
    return errorResponse(
      error,
      "PATCH /api/intake/[sessionId]/fields/[field]",
      "The founder fact could not be updated.",
      { 409: "The interview changed. Review the latest captured facts." },
    );
  }
}
