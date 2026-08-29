import Link from "next/link";

import { ApiErrorState } from "@/components/api-error-state";
import { DemoBadge, DraftStatusBadge } from "@/components/badges";
import { Page, PageHeader } from "@/components/primitives";
import { EmptyState, Note } from "@/components/states";
import { listDrafts } from "@/lib/api";
import { formatTimestamp, isDemo } from "@/lib/format";
import { FIELD_STATUSES } from "@/lib/types";
import type { DraftResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * Every draft, whether or not an inbox item still points at it. A draft is
 * the thing that saved the founder hours of writing — it does not get to be
 * reachable only through an item that may have been dismissed or never
 * created.
 */
function DraftRow({ payload }: { payload: DraftResponse }) {
  const { draft, counts } = payload;
  const total = FIELD_STATUSES.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
  const needsYou = counts.NEEDS_FOUNDER ?? 0;

  return (
    <li>
      <Link
        href={`/drafts/${encodeURIComponent(draft.draft_id)}`}
        className="block px-4 py-4 transition-colors hover:bg-sunk sm:px-5"
      >
        <div className="flex flex-wrap items-center gap-2.5">
          <DraftStatusBadge status={draft.status} />
          {isDemo(draft.form_name) ? <DemoBadge /> : null}
          <span className="text-xs text-ink-muted">
            Prepared {formatTimestamp(draft.created_at)}
          </span>
        </div>

        <p className="mt-1.5 font-serif text-lg tracking-tight text-ink">
          {draft.form_name || "Application draft"}
        </p>

        <p className="mt-1 text-xs text-ink-muted">
          {total} {total === 1 ? "question" : "questions"} ·{" "}
          {needsYou === 0 ? (
            "none need you"
          ) : (
            <span className="font-medium text-alert">
              {needsYou} {needsYou === 1 ? "needs" : "need"} you
            </span>
          )}{" "}
          · opportunity{" "}
          <span className="font-mono text-ink-soft">{draft.opportunity_id}</span>
        </p>
      </Link>
    </li>
  );
}

/**
 * Every draft for the founder, including ones whose inbox item was dismissed or never created.
 */
export default async function DraftsPage() {
  let drafts: DraftResponse[] = [];
  let error: unknown = null;
  try {
    drafts = await listDrafts();
  } catch (caught) {
    error = caught;
  }

  return (
    <Page>
      <PageHeader
        eyebrow="Drafts"
        title="Every application Kairos prepared"
        lede={
          <>
            Each one was drafted from your knowledge base and audited sentence
            by sentence. Kairos prepares; you review and submit — no screen
            here does it for you.
          </>
        }
      />

      {error ? (
        <ApiErrorState error={error} what="your drafts" />
      ) : drafts.length === 0 ? (
        <EmptyState title="No drafts yet">
          A draft appears here when a run judges an opportunity worth your time
          and has enough grounded facts to start writing. Start a run from the
          briefing to have Kairos look.
        </EmptyState>
      ) : (
        <>
          <ul className="divide-y divide-rule overflow-hidden rounded-lg border border-rule bg-surface">
            {drafts.map((payload) => (
              <DraftRow key={payload.draft.draft_id} payload={payload} />
            ))}
          </ul>
          <div className="mt-6">
            <Note>
              Drafts are listed here even when the inbox item that pointed at
              them was dismissed or never created. Counts are computed by the
              backend in Python, never by a model.
            </Note>
          </div>
        </>
      )}
    </Page>
  );
}
