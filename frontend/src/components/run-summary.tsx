import Link from "next/link";

import { Badge } from "@/components/badges";
import { RunCounters } from "@/components/run-counters";
import { Note } from "@/components/states";
import {
  formatDuration,
  formatInt,
  formatRelative,
  formatTimestamp,
  formatUsd,
} from "@/lib/format";
import type { RunReport } from "@/lib/types";

/** Source failures are reported, never smoothed over. A silent partial run is a lie. */
export function SourceFailures({ report }: { report: RunReport }) {
  if (report.sources_failed.length === 0) return null;
  return (
    <div className="rounded-md border border-warn/40 bg-warn-soft px-4 py-3">
      <p className="text-sm font-medium text-ink">
        {report.sources_failed.length === 1
          ? "One source did not answer"
          : `${report.sources_failed.length} sources did not answer`}
      </p>
      <p className="mt-1 text-sm leading-relaxed text-ink-soft">
        This run saw less than a complete run would have. It is reported rather
        than hidden.
      </p>
      <ul className="mt-3 space-y-2">
        {report.sources_failed.map((failure, index) => (
          <li key={`${failure.source}-${index}`} className="text-sm text-ink-soft">
            <span className="font-mono text-xs uppercase tracking-wide text-warn">
              {failure.source}
            </span>{" "}
            — {failure.detail}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function HaltedBanner({ reason }: { reason: string }) {
  return (
    <div role="alert" className="rounded-md border border-alert/40 bg-alert-soft px-4 py-3">
      <p className="text-sm font-medium text-ink">This run halted before it finished</p>
      <p className="mt-1 text-sm leading-relaxed text-ink-soft">
        A cap fired or a dependency died. A halted run surfaces nothing rather
        than delivering a partial digest.
      </p>
      <p className="mt-2 font-mono text-xs text-alert">{reason}</p>
    </div>
  );
}

/** Duration, tokens, spend, statelessness — the cost of the run, stated plainly. */
export function RunVitals({ report }: { report: RunReport }) {
  const items = [
    {
      label: "Started",
      value: formatTimestamp(report.started_at),
      hint: formatRelative(report.started_at),
    },
    { label: "Duration", value: formatDuration(report.duration_s), hint: undefined },
    { label: "Tokens", value: formatInt(report.usage.total_tokens), hint: undefined },
    { label: "Estimated spend", value: formatUsd(report.usage.usd_estimate), hint: undefined },
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
      {items.map((item) => (
        <div key={item.label}>
          <dt className="text-[11px] font-medium uppercase tracking-[0.12em] text-ink-muted">
            {item.label}
          </dt>
          <dd className="mt-0.5 text-sm tabular-nums text-ink">
            {item.value}
            {item.hint ? (
              <span className="ml-1.5 text-xs text-ink-muted">({item.hint})</span>
            ) : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function RunSummary({
  report,
  showLink = true,
}: {
  report: RunReport;
  showLink?: boolean;
}) {
  const nothingSurfaced = report.surfaced === 0 && !report.halted_reason;

  return (
    <div className="space-y-5">
      {report.halted_reason ? <HaltedBanner reason={report.halted_reason} /> : null}

      <RunCounters report={report} />

      <div className="flex flex-wrap items-center gap-3">
        {report.stateless ? (
          <Badge
            tone="warn"
            title="Memory was unavailable, so this run had no history to compare against."
          >
            Stateless run
          </Badge>
        ) : null}
        {report.finished_at === null && !report.halted_reason ? (
          <Badge tone="neutral">No finish time recorded</Badge>
        ) : null}
        {showLink ? (
          <Link
            href={`/runs/${encodeURIComponent(report.run_id)}`}
            className="text-sm text-accent underline underline-offset-4 hover:text-ink"
          >
            See everything this run threw away
          </Link>
        ) : null}
      </div>

      <RunVitals report={report} />

      <SourceFailures report={report} />

      {nothingSurfaced ? (
        <Note>
          Nothing surfaced this run. That is a result, not a failure — Kairos
          looked at {formatInt(report.scanned)}{" "}
          {report.scanned === 1 ? "opportunity" : "opportunities"} and judged
          that none were worth your time yet. Every discard has a reason you can
          read.
        </Note>
      ) : null}

      {report.notes.length > 0 ? (
        <div>
          <h3 className="text-[11px] font-medium uppercase tracking-[0.12em] text-ink-muted">
            Run notes
          </h3>
          <ul className="mt-2 space-y-1.5">
            {report.notes.map((note, index) => (
              <li key={index} className="text-sm leading-relaxed text-ink-soft">
                {note}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
