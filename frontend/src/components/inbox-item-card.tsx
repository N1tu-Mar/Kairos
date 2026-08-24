import Link from "next/link";

import { DemoBadge, InboxKindBadge, PassiveBadge } from "@/components/badges";
import { isDemo, splitHeadline } from "@/lib/format";
import type { InboxItem } from "@/lib/types";

/**
 * One surfaced opportunity.
 *
 * The headline is composed in Python (`agent/scout.py::_headline`) and
 * already carries the award ceiling, the days remaining and the effort
 * estimate. It is split on its separator for typography only — the facts are
 * rendered exactly as the backend wrote them.
 */
export function InboxItemCard({ item }: { item: InboxItem }) {
  const { title, facts } = splitHeadline(item.headline);
  const demo = isDemo(item.headline) || isDemo(item.summary);
  const assessment = item.assessment;

  return (
    <article className="rounded-lg border border-rule bg-surface p-5 sm:p-6">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <InboxKindBadge kind={item.kind} />
        {item.passive ? <PassiveBadge /> : null}
        {demo ? <DemoBadge /> : null}
      </div>

      <h3 className="font-serif text-xl leading-snug tracking-tight text-ink">
        {title}
      </h3>

      {facts.length > 0 ? (
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
      ) : null}

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

        {item.draft_id ? (
          <Link
            href={`/drafts/${encodeURIComponent(item.draft_id)}`}
            className="rounded-md border border-rule-strong bg-sunk px-3.5 py-1.5 text-sm font-medium text-ink transition-colors hover:border-accent hover:bg-accent-soft"
          >
            Review the draft
          </Link>
        ) : (
          <span className="text-xs text-ink-muted">
            No draft — nothing was drafted for this one
          </span>
        )}
      </div>
    </article>
  );
}
