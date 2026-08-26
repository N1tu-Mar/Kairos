import { NextResponse } from "next/server";

import { ApiError, httpStatusFor, setInboxState } from "@/lib/api";
import { INBOX_STATES } from "@/lib/types";
import type { InboxState } from "@/lib/types";

/**
 * Thin proxy over `PATCH /inbox/{item_id}`.
 *
 * The backend accepts exactly one mutable field — `state` — and so does this
 * route. Everything else on an inbox item is what the run decided, and an
 * audit trail you can edit is not one.
 */

export const dynamic = "force-dynamic";

function isInboxState(value: unknown): value is InboxState {
  return (
    typeof value === "string" && (INBOX_STATES as string[]).includes(value)
  );
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ itemId: string }> },
) {
  const { itemId } = await params;

  let state: InboxState;
  try {
    const body = (await request.json()) as { state?: unknown };
    if (!isInboxState(body.state)) {
      return NextResponse.json(
        { error: `state must be one of: ${INBOX_STATES.join(", ")}.` },
        { status: 400 },
      );
    }
    state = body.state;
  } catch {
    return NextResponse.json(
      { error: "Request body must be JSON." },
      { status: 400 },
    );
  }

  try {
    const item = await setInboxState(decodeURIComponent(itemId), state);
    return NextResponse.json(item);
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json(
        { error: error.userMessage, detail: error.message, kind: error.kind },
        { status: httpStatusFor(error) },
      );
    }
    return NextResponse.json(
      {
        error: "The item's state could not be updated.",
        detail: error instanceof Error ? error.message : String(error),
        kind: "unknown",
      },
      { status: 500 },
    );
  }
}
