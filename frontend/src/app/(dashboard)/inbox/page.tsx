import { ApiErrorState } from "@/components/api-error-state";
import { InboxFilter, parseInboxView } from "@/components/inbox-filter";
import { InboxItemCard } from "@/components/inbox-item-card";
import { EligibilityQuestionCard } from "@/components/eligibility-question-card";
import { Page, PageHeader } from "@/components/primitives";
import { EmptyState } from "@/components/states";
import {
  getInbox,
  getOpportunities,
  listEligibilityQuestions,
} from "@/lib/api";
import type { EligibilityQuestion, InboxItem, Opportunity } from "@/lib/types";

export const dynamic = "force-dynamic";

const EMPTY_COPY: Record<string, { title: string; body: string }> = {
  active: {
    title: "Nothing is waiting on you",
    body: "No opportunity has cleared the escalation policy yet. Kairos surfaces only what it judged worth interrupting you for, so an empty list means it looked and decided not to.",
  },
  passive: {
    title: "Nothing in the also-found list",
    body: "This list holds opportunities that cleared judgment but fell past the per-run surfacing cap. There are none right now.",
  },
  needs_you: {
    title: "No questions need your answer",
    body: "Kairos has no current opportunity waiting on a founder-only eligibility fact.",
  },
  all: {
    title: "Your inbox is empty",
    body: "Nothing has been surfaced yet. Start a run from the briefing to have Kairos look.",
  },
};

/**
 * Everything surfaced to the founder, filtered by the `view` query parameter.
 *
 * An unrecognised `view` falls back rather than rendering an empty list —
 * see `parseInboxView`.
 */
export default async function InboxPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const view = parseInboxView(params.view);

  let items: InboxItem[] = [];
  let questions: EligibilityQuestion[] = [];
  let error: unknown = null;
  // Structured rows for the cards. A row that fails to resolve just falls
  // back to the composed headline — it never fails the page.
  let opportunities = new Map<string, Opportunity>();
  try {
    [items, questions] = await Promise.all([
      getInbox(),
      listEligibilityQuestions("pending"),
    ]);
    opportunities = await getOpportunities(items.map((i) => i.opportunity_id));
  } catch (caught) {
    error = caught;
  }

  const active = items.filter((item) => !item.passive);
  const passive = items.filter((item) => item.passive);
  const shown = view === "active" ? active : view === "passive" ? passive : items;
  const empty = EMPTY_COPY[view];

  return (
    <Page>
      <PageHeader
        eyebrow="Inbox"
        title="What Kairos surfaced"
        lede={
          <>
            Each item names what the Assessor judged and what stands in the way.
            A draft link means Kairos already wrote most of the application,
            for you to review and never to submit.
          </>
        }
      />

      {error ? (
        <ApiErrorState error={error} what="your inbox" />
      ) : (
        <>
          <InboxFilter
            view={view}
            counts={{
              active: active.length,
              needs_you: questions.length,
              passive: passive.length,
              all: items.length,
            }}
          />

          <div className="mt-6 space-y-4">
            {view === "needs_you" ? (
              questions.length === 0 ? (
                <EmptyState title={empty.title}>{empty.body}</EmptyState>
              ) : (
                questions.map((question) => (
                  <EligibilityQuestionCard
                    key={question.question_id}
                    question={question}
                  />
                ))
              )
            ) : shown.length === 0 ? (
              <EmptyState title={empty.title}>{empty.body}</EmptyState>
            ) : (
              shown.map((item) => (
                <InboxItemCard
                  key={item.item_id}
                  item={item}
                  opportunity={opportunities.get(item.opportunity_id) ?? null}
                />
              ))
            )}
          </div>
        </>
      )}
    </Page>
  );
}
