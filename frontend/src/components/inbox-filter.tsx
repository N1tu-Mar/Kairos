import Link from "next/link";

export type InboxView = "active" | "passive" | "all";

/**
 * Read the inbox view out of a query-string value, defaulting on anything unrecognised.
 *
 * A URL is user-editable, so an unknown value falls back rather than
 * rendering an empty list that looks like "nothing was found".
 */
export function parseInboxView(raw: string | string[] | undefined): InboxView {
  const value = Array.isArray(raw) ? raw[0] : raw;
  return value === "passive" || value === "all" ? value : "active";
}

/**
 * Active recommendations and the passive "also found" list are different
 * claims, so they get different tabs rather than a mixed list with a marker.
 * Plain links: the filter works with JavaScript disabled.
 */
export function InboxFilter({
  view,
  counts,
}: {
  view: InboxView;
  counts: Record<InboxView, number>;
}) {
  const tabs: { key: InboxView; label: string; hint: string }[] = [
    {
      key: "active",
      label: "Recommendations",
      hint: "Surfaced and notified. These are the ones asking for your time.",
    },
    {
      key: "passive",
      label: "Also found",
      hint: "Past the per-run surfacing cap. Listed, never notified.",
    },
    { key: "all", label: "Everything", hint: "Both lists together." },
  ];

  return (
    <div className="border-b border-rule">
      <nav aria-label="Inbox filter" className="-mb-px flex flex-wrap gap-1">
        {tabs.map((tab) => {
          const active = tab.key === view;
          return (
            <Link
              key={tab.key}
              href={tab.key === "active" ? "/inbox" : `/inbox?view=${tab.key}`}
              aria-current={active ? "page" : undefined}
              title={tab.hint}
              className={`border-b-2 px-3.5 py-2.5 text-sm transition-colors ${
                active
                  ? "border-accent font-medium text-ink"
                  : "border-transparent text-ink-muted hover:text-ink"
              }`}
            >
              {tab.label}
              <span className="ml-2 tabular-nums text-xs text-ink-muted">
                {counts[tab.key]}
              </span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
