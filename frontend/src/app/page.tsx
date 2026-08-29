import Link from "next/link";

import { ApiErrorState } from "@/components/api-error-state";
import { InboxItemCard } from "@/components/inbox-item-card";
import { ManualRunControl } from "@/components/manual-run";
import { RunSummary } from "@/components/run-summary";
import { SchedulerFailures } from "@/components/scheduler-failures";
import { ScraperCandidates } from "@/components/scraper-candidates";
import { EmptyState, Note } from "@/components/states";
import { Page, PageHeader, Section } from "@/components/primitives";
import {
  getInbox,
  getLatestRun,
  getOpportunities,
  getScraperCandidates,
  listSchedulerFailures,
} from "@/lib/api";
import { formatRelative, runHeadline } from "@/lib/format";
import type {
  InboxItem,
  Opportunity,
  RunReport,
  SchedulerFailure,
  ScraperCandidateGroups,
} from "@/lib/types";

/**
 * The briefing.
 *
 * Data is fetched on the server. The browser never talks to FastAPI, never
 * runs the pipeline, and holds no credentials.
 */
export const dynamic = "force-dynamic";

/**
 * The briefing: latest run, what it surfaced, and the scraper review queues.
 *
 * Each data source is fetched into its own try/catch and its own error
 * variable, so one dead endpoint degrades its section rather than failing
 * the page. That is the pattern every page here follows.
 */
export default async function BriefingPage() {
  let report: RunReport | null = null;
  let inbox: InboxItem[] = [];
  let runError: unknown = null;
  let inboxError: unknown = null;
  let scraperError: unknown = null;
  let scraperCandidates: ScraperCandidateGroups = {};

  let failures: SchedulerFailure[] = [];

  const [runResult, inboxResult, failureResult, scraperResult] =
    await Promise.allSettled([
      getLatestRun(),
      getInbox(),
      listSchedulerFailures(),
      getScraperCandidates(),
    ]);

  if (runResult.status === "fulfilled") report = runResult.value;
  else runError = runResult.reason;

  if (inboxResult.status === "fulfilled") inbox = inboxResult.value;
  else inboxError = inboxResult.reason;

  // A failure to read the failure log is not itself worth an alarm on the
  // briefing — the run summary below already reports a backend that is down.
  if (failureResult.status === "fulfilled") failures = failureResult.value;

  if (scraperResult.status === "fulfilled") {
    scraperCandidates = scraperResult.value;
  } else {
    scraperError = scraperResult.reason;
  }

  const active = inbox.filter((item) => !item.passive);
  const passive = inbox.filter((item) => item.passive);

  // Structured rows for the cards shown below. Failure to resolve one falls
  // back to the composed headline; it never breaks the briefing.
  let opportunities = new Map<string, Opportunity>();
  try {
    opportunities = await getOpportunities(
      active.slice(0, 3).map((item) => item.opportunity_id),
    );
  } catch {
    // Fall back to headlines.
  }

  return (
    <Page>
      <PageHeader
        eyebrow="Your briefing"
        title={
          report
            ? runHeadline(report)
            : runError
              ? "The briefing is unavailable"
              : "Nothing has run yet"
        }
        lede={
          report ? (
            <>
              Last run {formatRelative(report.started_at)}. The number that
              matters is the one Kairos threw away. Every discard has a reason
              you can read.
            </>
          ) : null
        }
      />

      {failures.length > 0 ? (
        <Section title="Runs that did not happen">
          <SchedulerFailures failures={failures} />
        </Section>
      ) : null}

      <Section
        title="Latest run"
        description="What the last run saw, what it cost, and what it could not reach."
      >
        {runError ? (
          <ApiErrorState error={runError} what="the latest run" />
        ) : report ? (
          <RunSummary report={report} />
        ) : (
          <EmptyState title="No run has been recorded yet">
            Kairos has not looked for anything on your behalf so far. Start one
            by hand below. It will scan, filter, judge, and write down every
            decision it makes.
          </EmptyState>
        )}
      </Section>

      <Section
        title="Research queue"
        description="Search-discovered candidates waiting for review, split by where the scraper looked."
      >
        {scraperError ? (
          <ApiErrorState error={scraperError} what="the scraper candidates" />
        ) : (
          <ScraperCandidates groups={scraperCandidates} />
        )}
      </Section>

      <Section
        title="What surfaced"
        description="The only opportunities Kairos decided were worth interrupting you for."
        actions={
          inbox.length > 0 ? (
            <Link
              href="/inbox"
              className="text-sm text-accent underline underline-offset-4 hover:text-ink"
            >
              Open the full inbox
            </Link>
          ) : null
        }
      >
        {inboxError ? (
          <ApiErrorState error={inboxError} what="your inbox" />
        ) : active.length === 0 ? (
          <EmptyState title="Nothing is waiting on you">
            {report && report.surfaced === 0
              ? "The last run surfaced nothing. Kairos looked and judged that none of what it found was worth your time. That is a result, not a fault."
              : "No active recommendations right now."}
          </EmptyState>
        ) : (
          <div className="space-y-4">
            {active.slice(0, 3).map((item) => (
              <InboxItemCard
                key={item.item_id}
                item={item}
                opportunity={opportunities.get(item.opportunity_id) ?? null}
              />
            ))}
            {active.length > 3 ? (
              <Link
                href="/inbox"
                className="inline-block text-sm text-accent underline underline-offset-4 hover:text-ink"
              >
                {active.length - 3} more in the inbox
              </Link>
            ) : null}
          </div>
        )}

        {passive.length > 0 ? (
          <div className="mt-5">
            <Note>
              {passive.length} more{" "}
              {passive.length === 1 ? "opportunity is" : "opportunities are"}{" "}
              listed under <em>also found</em>. They sit past the per-run
              surfacing cap, so they are visible but never notified.{" "}
              <Link
                href="/inbox?view=passive"
                className="text-accent underline underline-offset-4"
              >
                See them
              </Link>
              .
            </Note>
          </div>
        ) : null}
      </Section>

      <Section title="Start a run">
        <ManualRunControl />
      </Section>
    </Page>
  );
}
