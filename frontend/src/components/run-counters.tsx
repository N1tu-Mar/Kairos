import type { RunReport } from "@/lib/types";
import { formatInt, runCounters } from "@/lib/format";

/**
 * "Scanned 214. Discarded 198. Judged 16. Surfaced 3."
 *
 * The counters come from the RunReport verbatim. Nothing is derived, summed
 * or re-labelled here, because the discard number is the claim being made.
 */
export function RunCounters({
  report,
  size = "large",
}: {
  report: RunReport;
  size?: "large" | "small";
}) {
  const counters = runCounters(report);
  return (
    <dl
      className={
        size === "large"
          ? "grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-rule bg-rule sm:grid-cols-4"
          : "grid grid-cols-4 gap-4"
      }
    >
      {counters.map((counter) => (
        <div
          key={counter.label}
          className={
            size === "large"
              ? "bg-surface px-5 py-5"
              : "border-l border-rule pl-3 first:border-l-0 first:pl-0"
          }
        >
          <dt
            className={
              size === "large"
                ? "text-xs font-medium uppercase tracking-[0.12em] text-ink-muted"
                : "text-[10px] font-medium uppercase tracking-[0.1em] text-ink-muted"
            }
            title={counter.hint}
          >
            {counter.label}
          </dt>
          <dd
            className={
              size === "large"
                ? "mt-1.5 font-serif text-3xl tabular-nums text-ink"
                : "font-serif text-lg tabular-nums text-ink"
            }
          >
            {formatInt(counter.value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
