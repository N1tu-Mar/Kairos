import { DemoBadge } from "@/components/badges";
import { EmptyState } from "@/components/states";
import { SKIP_STAGE_LABELS, isDemo, titleCase } from "@/lib/format";
import type { Rejection, SkipRecord } from "@/lib/types";

/**
 * The silent path, written down.
 *
 * This is a product surface, not a debug screen: "how do I know it isn't
 * hiding things?" should have an answer that is one click away and readable
 * without knowing anything about the pipeline.
 */

function groupBy<T>(items: T[], key: (item: T) => string): Map<string, T[]> {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const bucket = key(item);
    const existing = groups.get(bucket);
    if (existing) existing.push(item);
    else groups.set(bucket, [item]);
  }
  return groups;
}

/**
 * Everything the deterministic filter dropped, with the founder value beside the required one.
 *
 * This is the answer to "how do I know it isn't hiding things?" — each row
 * names the check that fired, so a disputed verdict points at a rule rather
 * than at a model.
 */
export function RejectionTable({ rejections }: { rejections: Rejection[] }) {
  if (rejections.length === 0) {
    return (
      <EmptyState title="Nothing was rejected deterministically">
        No opportunity in this run failed a hard eligibility check.
      </EmptyState>
    );
  }

  const groups = [...groupBy(rejections, (r) => r.check).entries()].sort(
    (a, b) => b[1].length - a[1].length,
  );

  return (
    <div className="space-y-6">
      {groups.map(([check, rows]) => (
        <div key={check}>
          <div className="mb-2 flex flex-wrap items-baseline gap-2">
            <h3 className="font-mono text-sm font-semibold tracking-tight text-ink">
              {check}
            </h3>
            <span className="text-xs text-ink-muted">
              {rows.length} {rows.length === 1 ? "opportunity" : "opportunities"}{" "}
              dropped by this check
            </span>
          </div>

          <div className="overflow-x-auto rounded-lg border border-rule">
            <table className="w-full min-w-[44rem] border-collapse text-sm">
              <caption className="sr-only">
                Opportunities rejected by the {check} check
              </caption>
              <thead>
                <tr className="border-b border-rule bg-sunk text-left">
                  <th scope="col" className="px-4 py-2.5 font-medium text-ink-muted">
                    Opportunity
                  </th>
                  <th scope="col" className="px-4 py-2.5 font-medium text-ink-muted">
                    Why
                  </th>
                  <th scope="col" className="px-4 py-2.5 font-medium text-ink-muted">
                    You
                  </th>
                  <th scope="col" className="px-4 py-2.5 font-medium text-ink-muted">
                    Required
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((rejection, index) => (
                  <tr
                    key={`${rejection.opportunity_id}-${index}`}
                    className="border-b border-rule last:border-b-0 align-top"
                  >
                    <td className="px-4 py-3 text-ink">
                      <span className="block">{rejection.opportunity_title}</span>
                      {isDemo(rejection.opportunity_title) ? (
                        <span className="mt-1.5 inline-block">
                          <DemoBadge />
                        </span>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 text-ink-soft">{rejection.detail}</td>
                    <td className="px-4 py-3 font-mono text-xs text-ink-soft">
                      {rejection.founder_value}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-ink-soft">
                      {rejection.required_value}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * Opportunities the pipeline judged and then chose not to surface, grouped by stage.
 *
 * Distinct from the rejection table: these passed the hard filter and were
 * dropped by the Assessor or the escalation policy, which is a judgment
 * rather than a rule.
 */
export function SkipList({ skips }: { skips: SkipRecord[] }) {
  if (skips.length === 0) {
    return (
      <EmptyState title="Nothing was skipped after judgment">
        Everything that survived the deterministic filter also cleared the
        Assessor and the escalation policy.
      </EmptyState>
    );
  }

  const groups = [...groupBy(skips, (s) => s.stage).entries()];

  return (
    <div className="space-y-6">
      {groups.map(([stage, rows]) => (
        <div key={stage}>
          <div className="mb-2 flex flex-wrap items-baseline gap-2">
            <h3 className="font-serif text-base tracking-tight text-ink">
              {SKIP_STAGE_LABELS[stage] ?? titleCase(stage)}
            </h3>
            <span className="text-xs text-ink-muted">
              {rows.length} {rows.length === 1 ? "opportunity" : "opportunities"}
            </span>
          </div>

          <ul className="divide-y divide-rule rounded-lg border border-rule bg-surface">
            {rows.map((skip, index) => (
              <li key={`${skip.opportunity_id}-${index}`} className="px-4 py-3.5">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium text-ink">
                    {skip.opportunity_title}
                  </p>
                  {isDemo(skip.opportunity_title) ? <DemoBadge /> : null}
                </div>
                <p className="mt-1 text-sm leading-relaxed text-ink-soft">
                  {skip.reason}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
