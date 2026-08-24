import Link from "next/link";

import { ApiErrorState } from "@/components/api-error-state";
import { InboxItemCard } from "@/components/inbox-item-card";
import { ManualRunControl } from "@/components/manual-run";
import { RunSummary } from "@/components/run-summary";
import { EmptyState, Note } from "@/components/states";
import { Page, PageHeader, Section } from "@/components/primitives";
import { getInbox, getLatestRun } from "@/lib/api";
import { formatRelative, runHeadline } from "@/lib/format";
import type { InboxItem, RunReport } from "@/lib/types";

/**
 * The briefing.
 *
 * Data is fetched on the server. The browser never talks to FastAPI, never
 * runs the pipeline, and holds no credentials.
 */
export const dynamic = "force-dynamic";

export default async function BriefingPage() {
  let report: RunReport | null = null;
  let inbox: InboxItem[] = [];
  let runError: unknown = null;
  let inboxError: unknown = null;

  const [runResult, inboxResult] = await Promise.allSettled([
    getLatestRun(),
    getInbox(),
  ]);

  if (runResult.status === "fulfilled") report = runResult.value;
  else runError = runResult.reason;

  if (inboxResult.status === "fulfilled") inbox = inboxResult.value;
  else inboxError = inboxResult.reason;

  const active = inbox.filter((item) => !item.passive);
  const passive = inbox.filter((item) => item.passive);

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
              matters is the one Kairos threw away — every discard has a reason
              you can read.
            </>
          ) : null
        }
      />

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
            by hand below — it will scan, filter, judge, and write down every
            decision it makes.
          </EmptyState>
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
              ? "The last run surfaced nothing. Kairos looked and judged that none of what it found was worth your time — that is a result, not a fault."
              : "No active recommendations right now."}
          </EmptyState>
        ) : (
          <div className="space-y-4">
            {active.slice(0, 3).map((item) => (
              <InboxItemCard key={item.item_id} item={item} />
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
              listed under <em>also found</em> — past the per-run surfacing cap,
              so they are visible but never notified.{" "}
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
