import Link from "next/link";
import type { ReactNode } from "react";

import {
  Badge,
  DemoBadge,
  InboxKindBadge,
  InboxStateBadge,
  PassiveBadge,
} from "@/components/badges";
import { InboxStateControl } from "@/components/inbox-state-control";
import {
  daysUntil,
  formatAwardRange,
  formatDate,
  isDemo,
  splitHeadline,
} from "@/lib/format";
import type { InboxItem, Opportunity } from "@/lib/types";

/**
 * One surfaced opportunity.
 *
 * Facts come from the structured `Opportunity` row the run persisted —
 * award range, deadline, funder and the source URL are fields served by
 * `GET /opportunities/{id}`, not text parsed back out of prose. When the row
 * cannot be resolved, the card falls back to the headline the backend
 * composed in Python (`agent/scout.py::_headline`), split on its separator
 * for typography only. Neither path invents a value.
 */

/** Deadline rendered from the structured field, with urgency carried by tone. */
function DeadlineFact({ opportunity }: { opportunity: Opportunity }) {
  if (opportunity.rolling) return <span>Rolling deadline</span>;
  if (!opportunity.deadline) return null;
  const days = daysUntil(opportunity.deadline);
  const label = formatDate(opportunity.deadline);
  if (days == null) return <span>Due {label}</span>;
  if (days < 0) {
    return (
      <span className="font-medium text-alert">
        Deadline passed · {label}
      </span>
    );
  }
  return (
    <span className={days <= 14 ? "font-medium text-warn" : undefined}>
      Due {label} · {days} {days === 1 ? "day" : "days"} left
    </span>
  );
}

/**
 * Award, deadline and source read off the `Opportunity` row rather than parsed out of the headline.
 *
 * Renders nothing when the opportunity failed to load — the card still
 * shows the headline the run composed, so a missing row degrades the card
 * instead of breaking the page.
 */
function StructuredFacts({ opportunity }: { opportunity: Opportunity }) {
  const award = formatAwardRange(opportunity.award_min, opportunity.award_max);
  const facts: ReactNode[] = [];
  if (opportunity.funder) facts.push(<span key="funder">{opportunity.funder}</span>);
  if (award) facts.push(<span key="award">{award}</span>);
  const deadline = DeadlineFact({ opportunity });
  if (deadline) facts.push(<span key="deadline">{deadline}</span>);
  if (facts.length === 0) return null;
  return (
    <ul className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-muted">
      {facts.map((fact, index) => (
        <li key={index} className="flex items-center gap-3">
          {index > 0 ? (
            <span aria-hidden="true" className="text-rule-strong">
              ·
            </span>
          ) : null}
          {fact}
        </li>
      ))}
    </ul>
  );
}

/** Fallback when the opportunity row could not be resolved. */
function HeadlineFacts({ facts }: { facts: string[] }) {
  if (facts.length === 0) return null;
  return (
    <ul className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-muted">
      {facts.map((fact, index) => (
        <li key={index} className="flex items-center gap-3">
          {index > 0 ? (
            <span aria-hidden="true" className="text-rule-strong">
              ·
            </span>
          ) : null}
          <span>{fact}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * One surfaced opportunity: why it was surfaced, what it is, and what to do about it.
 */
export function InboxItemCard({
  item,
  opportunity = null,
}: {
  item: InboxItem;
  /** The persisted row, when the page could resolve it. Null falls back to the headline. */
  opportunity?: Opportunity | null;
}) {
  const { title, facts } = splitHeadline(item.headline);
  const demo =
    isDemo(item.headline) ||
    isDemo(item.summary) ||
    isDemo(opportunity?.title);
  const assessment = item.assessment;
  const dismissed = item.state === "dismissed";

  return (
    <article
      className={`rounded-lg border border-rule bg-surface p-5 sm:p-6 ${
        dismissed ? "opacity-60" : ""
      }`}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <InboxKindBadge kind={item.kind} />
        <InboxStateBadge state={item.state} />
        {item.passive ? <PassiveBadge /> : null}
        {demo ? <DemoBadge /> : null}
        {opportunity && !opportunity.verified ? (
          <Badge
            tone="warn"
            title="No human has opened this row's source URL and confirmed it yet."
          >
            Unverified source
          </Badge>
        ) : null}
      </div>

      <h3 className="font-serif text-xl leading-snug tracking-tight text-ink">
        {opportunity?.title ?? title}
      </h3>

      {opportunity ? (
        <StructuredFacts opportunity={opportunity} />
      ) : (
        <HeadlineFacts facts={facts} />
      )}

      {item.summary ? (
        <p className="mt-4 whitespace-pre-line text-[15px] leading-relaxed text-ink-soft">
          {item.summary}
        </p>
      ) : null}

      {assessment?.blocker ? (
        <div className="mt-4 rounded-md border-l-2 border-warn bg-warn-soft px-4 py-3">
          <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-warn">
            {assessment.blocker_founder_resolvable
              ? "In the way, and you can move it"
              : "In the way"}
          </p>
          <p className="mt-1 text-sm leading-relaxed text-ink-soft">
            {assessment.blocker}
          </p>
        </div>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-4">
        <dl className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-ink-muted">
          {assessment ? (
            <div className="flex items-center gap-1.5">
              <dt>Estimated effort</dt>
              <dd className="tabular-nums text-ink">
                {assessment.effort_hours.toFixed(1)} h
              </dd>
            </div>
          ) : null}
          <div className="flex items-center gap-1.5">
            <dt>Opportunity</dt>
            <dd className="font-mono text-ink-soft">{item.opportunity_id}</dd>
          </div>
        </dl>

        <div className="flex flex-wrap items-center gap-3">
          {opportunity?.source_url ? (
            <a
              href={opportunity.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-accent underline underline-offset-4 hover:text-ink"
            >
              Open the funder&rsquo;s page ↗
            </a>
          ) : null}
          {item.draft_id ? (
            <Link
              href={`/drafts/${encodeURIComponent(item.draft_id)}`}
              className="rounded-md border border-rule-strong bg-sunk px-3.5 py-1.5 text-sm font-medium text-ink transition-colors hover:border-accent hover:bg-accent-soft"
            >
              Review the draft
            </Link>
          ) : (
            <span className="text-xs text-ink-muted">
              Nothing was drafted for this one
            </span>
          )}
        </div>
      </div>

      <div className="mt-4 flex justify-end">
        <InboxStateControl itemId={item.item_id} state={item.state} />
      </div>
    </article>
  );
}
