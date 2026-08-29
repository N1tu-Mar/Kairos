"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";

import type { InboxState } from "@/lib/types";

/**
 * The founder's side of the audit trail: what they did with a surfaced item.
 *
 * `state` is the only thing the backend lets anyone change about an inbox
 * item, and this control offers exactly that — no editing of the verdict,
 * the headline or the assessment. "Opened" is deliberately not set
 * automatically: a state the founder did not choose is a record of nothing.
 */

const ACTIONS: { state: InboxState; label: string; busy: string }[] = [
  { state: "applied", label: "Mark applied", busy: "Marking…" },
  { state: "dismissed", label: "Dismiss", busy: "Dismissing…" },
];

/**
 * The opened / dismissed / applied buttons on one inbox item.
 *
 * The only field on an inbox item a person may change. Everything else is
 * what the run decided.
 */
export function InboxStateControl({
  itemId,
  state,
}: {
  itemId: string;
  state: InboxState;
}) {
  const router = useRouter();
  const [pending, setPending] = useState<InboxState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  /**
   * PATCH the new state, then refresh so the server's value is what renders.
   *
   * No optimistic update: the state shown is always the state the backend
   * confirmed, so a failed write cannot leave the UI claiming a change that
   * did not happen. Re-clicking the current state is a no-op rather than a
   * redundant request.
   */
  async function setState(next: InboxState) {
    if (inFlight.current || next === state) return;
    inFlight.current = true;
    setPending(next);
    setError(null);

    try {
      const response = await fetch(
        `/api/inbox/${encodeURIComponent(itemId)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ state: next }),
        },
      );
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as {
          error?: string;
        } | null;
        setError(body?.error ?? `The backend returned ${response.status}.`);
        return;
      }
      // The card and the list around it are server-rendered from the same
      // data this just changed. Re-render them instead of patching the DOM.
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "The request did not reach the API.",
      );
    } finally {
      inFlight.current = false;
      setPending(null);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1.5">
      <div className="flex items-center gap-2">
        {state !== "new" ? (
          <button
            type="button"
            onClick={() => setState("new")}
            disabled={pending !== null}
            className="text-xs text-ink-muted underline underline-offset-4 transition-colors hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending === "new" ? "Restoring…" : "Restore to new"}
          </button>
        ) : null}
        {ACTIONS.filter((action) => action.state !== state).map((action) => (
          <button
            key={action.state}
            type="button"
            onClick={() => setState(action.state)}
            disabled={pending !== null}
            className="rounded-md border border-rule px-3 py-1.5 text-xs font-medium text-ink-soft transition-colors hover:border-rule-strong hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending === action.state ? action.busy : action.label}
          </button>
        ))}
      </div>
      {error ? (
        <p role="alert" className="text-xs text-alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
