import { NextResponse } from "next/server";

import { setInboxState } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
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

/**
 * Narrow an unknown body value to an `InboxState`.
 *
 * Checked against the shared `INBOX_STATES` list rather than a literal
 * union written out again here, so adding a state in one place cannot leave
 * this validator behind.
 */
function isInboxState(value: unknown): value is InboxState {
  return (
    typeof value === "string" && (INBOX_STATES as string[]).includes(value)
  );
}

/**
 * Set an item's state. 400 for a body that is not one of the known states.
 *
 * The id is taken from the path and passed through; ownership is the
 * backend's check, not this route's.
 */
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
    return errorResponse(
      error,
      "PATCH /api/inbox/[itemId]",
      "The item's state could not be updated.",
    );
  }
}
