import { NextResponse } from "next/server";

import { sendIntakeMessage } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import type { IntakeMessageCreate } from "@/lib/types";

export const dynamic = "force-dynamic";

interface RouteContext {
  params: Promise<{ sessionId: string }>;
}

function isMessage(value: unknown): value is IntakeMessageCreate {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return (
    Object.keys(candidate).length === 3 &&
    typeof candidate.text === "string" &&
    candidate.text.trim().length > 0 &&
    candidate.text.length <= 8_000 &&
    typeof candidate.client_message_id === "string" &&
    candidate.client_message_id.length > 0 &&
    candidate.client_message_id.length <= 200 &&
    Number.isInteger(candidate.expected_revision) &&
    Number(candidate.expected_revision) >= 0
  );
}

/** Forward exactly the bounded message contract, never arbitrary client JSON. */
export async function POST(request: Request, context: RouteContext) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: "Request body must be JSON." },
      { status: 400 },
    );
  }
  if (!isMessage(body)) {
    return NextResponse.json(
      { error: "A message, idempotency key, and current revision are required." },
      { status: 400 },
    );
  }

  const { sessionId } = await context.params;
  try {
    return NextResponse.json(await sendIntakeMessage(sessionId, body));
  } catch (error) {
    return errorResponse(
      error,
      "POST /api/intake/[sessionId]/messages",
      "The message could not be sent.",
    );
  }
}
