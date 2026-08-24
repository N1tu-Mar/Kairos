"use client";

import { useEffect } from "react";

import { Page, PageHeader } from "@/components/primitives";
import { ErrorState } from "@/components/states";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("kairos dashboard error", error);
  }, [error]);

  return (
    <Page>
      <PageHeader eyebrow="Error" title="This screen did not render" />
      <ErrorState
        title="Something broke on the way to the page"
        message={error.message || "An unexpected error occurred."}
        hint={
          error.digest ? (
            <span className="font-mono text-xs">digest {error.digest}</span>
          ) : null
        }
        action={
          <button
            type="button"
            onClick={reset}
            className="rounded-md border border-rule-strong bg-surface px-3.5 py-1.5 text-sm font-medium text-ink hover:border-accent"
          >
            Try again
          </button>
        }
      />
    </Page>
  );
}
