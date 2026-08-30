import Link from "next/link";
import { notFound } from "next/navigation";

import { ApiErrorState } from "@/components/api-error-state";
import { Page, PageHeader, Section } from "@/components/primitives";
import { RunSummary } from "@/components/run-summary";
import { Note } from "@/components/states";
import { RejectionTable, SkipList } from "@/components/transparency";
import { getRun } from "@/lib/api";
import { formatInt, formatTimestamp, runHeadline } from "@/lib/format";
import type { RunReport } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * One run in full: its counters, every rejection, and every skip.
 *
 * 404s for a run id that is not this founder's — the backend scopes the
 * lookup, so a guessed id is indistinguishable from a missing one.
 */
export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;

  let report: RunReport | null = null;
  let error: unknown = null;
  try {
    report = await getRun(decodeURIComponent(runId));
  } catch (caught) {
    error = caught;
  }

  if (error) {
    return (
      <Page>
        <PageHeader eyebrow="Run detail" title="This run could not be loaded" />
        <ApiErrorState error={error} what="this run" />
      </Page>
    );
  }

  if (!report) notFound();

  const decisions = report.rejections.length + report.skips.length;

  return (
    <Page>
      <PageHeader
        eyebrow={`Run · ${formatTimestamp(report.started_at)}`}
        title={runHeadline(report)}
        lede={
          <>
            {formatInt(decisions)} recorded{" "}
            {decisions === 1 ? "decision" : "decisions"} below. Nothing here was
            written by a model. Every rejection names the deterministic check
            that fired, and every skip names the stage that made the call.
          </>
        }
        actions={
          <Link
            href="/runs"
            className="text-sm text-accent underline underline-offset-4 hover:text-ink"
          >
            All runs
          </Link>
        }
      />

      <Section title="What the run cost and reached">
        <RunSummary report={report} showLink={false} />
      </Section>

      <Section
        title="Rejected before any model saw it"
        description="The hard eligibility filter is pure Python reading structured fields. It cannot be argued with by text on a funding page, and it drops most of what a run finds."
      >
        <RejectionTable rejections={report.rejections} />
      </Section>

      <Section
        title="Judged, then held back"
        description="These cleared the filter but did not clear the Assessor or the escalation policy. The reason is the one recorded at the time, in full."
      >
        <SkipList skips={report.skips} />
      </Section>

      {report.notes.length > 0 || report.sources_failed.length > 0 ? (
        <Section title="Notes from this run">
          <Note>
            Run id <span className="font-mono text-xs">{report.run_id}</span>.
            Notes and source failures are recorded exactly as the pipeline wrote
            them, including when that makes the run look worse.
          </Note>
        </Section>
      ) : null}
    </Page>
  );
}
