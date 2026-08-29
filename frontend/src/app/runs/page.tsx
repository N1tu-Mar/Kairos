import Link from "next/link";

import { ApiErrorState } from "@/components/api-error-state";
import { Badge } from "@/components/badges";
import { Page, PageHeader } from "@/components/primitives";
import { EmptyState } from "@/components/states";
import { listRuns } from "@/lib/api";
import {
  formatDuration,
  formatInt,
  formatRelative,
  formatTimestamp,
} from "@/lib/format";
import type { RunReport } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * One run in the list: when it ran, its counters, and how many decisions it recorded.
 */
function RunRow({ report }: { report: RunReport }) {
  const decisions = report.rejections.length + report.skips.length;
  return (
    <li>
      <Link
        href={`/runs/${encodeURIComponent(report.run_id)}`}
        className="block px-4 py-4 transition-colors hover:bg-sunk sm:px-5"
      >
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="text-sm text-ink">
            {formatTimestamp(report.started_at)}
          </span>
          <span className="text-xs text-ink-muted">
            {formatRelative(report.started_at)}
          </span>
          {report.halted_reason ? <Badge tone="alert">Halted</Badge> : null}
          {report.sources_failed.length > 0 ? (
            <Badge tone="warn">
              {report.sources_failed.length} source
              {report.sources_failed.length === 1 ? "" : "s"} failed
            </Badge>
          ) : null}
          {report.stateless ? <Badge tone="neutral">Stateless</Badge> : null}
        </div>

        <p className="mt-1.5 font-serif text-lg tracking-tight text-ink">
          Scanned {formatInt(report.scanned)}. Discarded{" "}
          {formatInt(report.filtered_out)}. Judged {formatInt(report.judged)}.
          Surfaced {formatInt(report.surfaced)}.
        </p>

        <p className="mt-1 text-xs text-ink-muted">
          {formatDuration(report.duration_s)} ·{" "}
          {formatInt(report.usage.total_tokens)} tokens ·{" "}
          {formatInt(decisions)} recorded{" "}
          {decisions === 1 ? "decision" : "decisions"} you can read
        </p>
      </Link>
    </li>
  );
}

/**
 * Every run, newest first, capped at 50.
 *
 * Not paginated — older runs are reachable by id through `/runs/{runId}`,
 * which is what keeps the transparency trail from having a horizon.
 */
export default async function RunsPage() {
  let runs: RunReport[] = [];
  let error: unknown = null;
  try {
    runs = await listRuns(undefined, 50);
  } catch (caught) {
    error = caught;
  }

  return (
    <Page>
      <PageHeader
        eyebrow="Runs"
        title="Every run, and everything it threw away"
        lede={
          <>
            An agent&rsquo;s judgment is measured by what it discards silently.
            Kairos does not discard silently. Open any run to see the exact
            check that fired on every opportunity it dropped.
          </>
        }
      />

      {error ? (
        <ApiErrorState error={error} what="run history" />
      ) : runs.length === 0 ? (
        <EmptyState
          title="No runs recorded yet"
          action={
            <Link
              href="/"
              className="text-sm text-accent underline underline-offset-4 hover:text-ink"
            >
              Start one from the briefing
            </Link>
          }
        >
          Once a run completes, its full decision record appears here and stays.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-rule overflow-hidden rounded-lg border border-rule bg-surface">
          {runs.map((report) => (
            <RunRow key={report.run_id} report={report} />
          ))}
        </ul>
      )}
    </Page>
  );
}
