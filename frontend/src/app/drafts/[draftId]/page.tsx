import Link from "next/link";
import { notFound } from "next/navigation";

import { ApiErrorState } from "@/components/api-error-state";
import { DraftStatusBadge } from "@/components/badges";
import {
  DraftCountsSummary,
  DraftFieldCard,
  DraftFormName,
  GateOutcome,
} from "@/components/draft";
import { Page, PageHeader, Section } from "@/components/primitives";
import { EmptyState, Note } from "@/components/states";
import { getDraftOrNull } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import type { DraftResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * One draft: every question, its answer, where the answer came from, and the gate result.
 *
 * The gate result is rendered even when the draft passed, because "which
 * checks ran" is as much the point as whether they passed.
 */
export default async function DraftPage({
  params,
}: {
  params: Promise<{ draftId: string }>;
}) {
  const { draftId } = await params;

  let payload: DraftResponse | null = null;
  let error: unknown = null;
  try {
    payload = await getDraftOrNull(decodeURIComponent(draftId));
  } catch (caught) {
    error = caught;
  }

  if (error) {
    return (
      <Page>
        <PageHeader eyebrow="Draft" title="This draft could not be loaded" />
        <ApiErrorState error={error} what="this draft" />
      </Page>
    );
  }

  if (!payload) notFound();

  const { draft, counts } = payload;
  const needsFounder = draft.fields.filter((f) => f.status === "NEEDS_FOUNDER");
  const rest = draft.fields.filter((f) => f.status !== "NEEDS_FOUNDER");

  return (
    <Page>
      <PageHeader
        eyebrow="Draft"
        title={draft.form_name || "Application draft"}
        lede={
          <>
            Kairos prepared this application. It does not submit it, and it
            cannot — a person reads it, fills the gaps, and decides.
          </>
        }
        actions={
          <Link
            href="/inbox"
            className="text-sm text-accent underline underline-offset-4 hover:text-ink"
          >
            Back to inbox
          </Link>
        }
      />

      <div className="mb-8 flex flex-wrap items-center gap-3">
        <DraftStatusBadge status={draft.status} />
        <span className="text-sm text-ink-muted">
          <DraftFormName name={draft.form_name} />
        </span>
        <span className="text-xs text-ink-muted">
          Prepared {formatTimestamp(draft.created_at)}
        </span>
      </div>

      <Section title="Where this stands">
        <div className="space-y-6">
          <DraftCountsSummary counts={counts} />
          <GateOutcome gate={draft.gate_result} />
        </div>
      </Section>

      {needsFounder.length > 0 ? (
        <Section
          title="These need you"
          description="Kairos had no grounded answer for these, so it wrote nothing rather than something plausible."
        >
          <div className="space-y-4">
            {needsFounder.map((field) => (
              <DraftFieldCard key={field.field_id} field={field} />
            ))}
          </div>
        </Section>
      ) : null}

      <Section
        title={needsFounder.length > 0 ? "Already answered" : "The answers"}
        description="Every generated sentence carries the quote it came from. If a claim has no receipt, it did not survive the gate."
      >
        {rest.length === 0 ? (
          <EmptyState title="No answers were drafted">
            Nothing on this form could be answered from what Kairos knows about
            you yet.
          </EmptyState>
        ) : (
          <div className="space-y-4">
            {rest.map((field) => (
              <DraftFieldCard key={field.field_id} field={field} />
            ))}
          </div>
        )}
      </Section>

      <Note>
        Kairos stops here. Submitting a funding application is a decision with
        your name on it, and often requires an authorised representative of your
        institution — so the last step is always yours.
      </Note>
    </Page>
  );
}
