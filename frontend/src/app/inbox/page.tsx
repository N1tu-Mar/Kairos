import { ApiErrorState } from "@/components/api-error-state";
import { InboxFilter, parseInboxView } from "@/components/inbox-filter";
import { InboxItemCard } from "@/components/inbox-item-card";
import { Page, PageHeader } from "@/components/primitives";
import { EmptyState } from "@/components/states";
import { getInbox } from "@/lib/api";
import type { InboxItem } from "@/lib/types";

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
  all: {
    title: "Your inbox is empty",
    body: "Nothing has been surfaced yet. Start a run from the briefing to have Kairos look.",
  },
};

export default async function InboxPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const view = parseInboxView(params.view);

  let items: InboxItem[] = [];
  let error: unknown = null;
  try {
    items = await getInbox();
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
            A draft link means Kairos already wrote most of the application —
            for you to review, never to submit.
          </>
        }
      />

      {error ? (
        <ApiErrorState error={error} what="your inbox" />
      ) : (
        <>
          <InboxFilter
            view={view}
            counts={{ active: active.length, passive: passive.length, all: items.length }}
          />

          <div className="mt-6 space-y-4">
            {shown.length === 0 ? (
              <EmptyState title={empty.title}>{empty.body}</EmptyState>
            ) : (
              shown.map((item) => <InboxItemCard key={item.item_id} item={item} />)
            )}
          </div>
        </>
      )}
    </Page>
  );
}
