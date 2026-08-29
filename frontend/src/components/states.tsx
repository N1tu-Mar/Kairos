import type { ReactNode } from "react";

/**
 * Empty, error and loading states. Every data-backed view uses these rather
 * than rendering a blank region — an empty screen and a broken screen must
 * never look the same.
 */

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-rule-strong bg-sunk px-6 py-10 text-center">
      <p className="font-serif text-lg text-ink">{title}</p>
      {children ? (
        <div className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-muted">
          {children}
        </div>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

/**
 * The visible failure state. An error must never render as an empty region.
 *
 * `hint` is where the actionable half goes — what the reader can do — as
 * distinct from `message`, which says what went wrong.
 */
export function ErrorState({
  title = "Something did not load",
  message,
  hint,
  action,
}: {
  title?: string;
  message: string;
  hint?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-alert/40 bg-alert-soft px-6 py-6"
    >
      <p className="font-serif text-lg text-ink">{title}</p>
      <p className="mt-2 text-sm leading-relaxed text-ink-soft">{message}</p>
      {hint ? (
        <div className="mt-3 text-sm leading-relaxed text-ink-muted">{hint}</div>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/**
 * A shimmering placeholder block, sized by the caller.
 */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`kairos-pulse rounded bg-sunk ${className}`}
    />
  );
}

/**
 * A stack of skeleton lines for a whole region that is still loading.
 */
export function LoadingBlock({ label }: { label: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-3">
      <span className="sr-only">{label}</span>
      <Skeleton className="h-5 w-2/5" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

/** A short explanatory aside. Used to say what a screen is for, once. */
export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-md border-l-2 border-rule-strong bg-sunk px-4 py-3 text-sm leading-relaxed text-ink-soft">
      {children}
    </p>
  );
}
