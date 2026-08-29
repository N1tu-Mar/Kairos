import Link from "next/link";

import { Page, PageHeader } from "@/components/primitives";
import { EmptyState } from "@/components/states";

/**
 * The 404 page. Reached for an unknown route and for a resource the backend reports as missing.
 */
export default function NotFound() {
  return (
    <Page>
      <PageHeader eyebrow="Not found" title="There is nothing here" />
      <EmptyState
        title="Kairos has no record of that"
        action={
          <Link
            href="/"
            className="text-sm text-accent underline underline-offset-4 hover:text-ink"
          >
            Back to your briefing
          </Link>
        }
      >
        The run, draft or page you asked for is not in the database. If it was
        just created, the backend may not have written it yet.
      </EmptyState>
    </Page>
  );
}
