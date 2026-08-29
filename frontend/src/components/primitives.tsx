import type { ReactNode } from "react";

/** Layout and typographic primitives shared by every view. */

export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-10 sm:px-8 sm:py-14">
      {children}
    </div>
  );
}

/**
 * The heading block every page opens with: eyebrow, title, lede, actions.
 */
export function PageHeader({
  eyebrow,
  title,
  lede,
  actions,
}: {
  eyebrow?: string;
  title: string;
  lede?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-10 border-b border-rule pb-8">
      <div className="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="max-w-2xl">
          {eyebrow ? (
            <p className="mb-2 text-xs font-medium uppercase tracking-[0.14em] text-ink-muted">
              {eyebrow}
            </p>
          ) : null}
          <h1 className="font-serif text-3xl leading-tight tracking-tight text-ink sm:text-4xl">
            {title}
          </h1>
          {lede ? (
            <div className="mt-3 text-[15px] leading-relaxed text-ink-soft">
              {lede}
            </div>
          ) : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </div>
    </header>
  );
}

/**
 * A titled region with optional description and trailing actions.
 */
export function Section({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-12">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="font-serif text-xl tracking-tight text-ink">{title}</h2>
          {description ? (
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-muted">
              {description}
            </p>
          ) : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

/**
 * A bordered surface. Layout only — it carries no state and no semantics.
 */
export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border border-rule bg-surface p-5 sm:p-6 ${className}`}
    >
      {children}
    </div>
  );
}

/**
 * A label/value pair for the fact tables. Values wrap; labels do not.
 */
export function DefinitionRow({
  term,
  children,
}: {
  term: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 border-b border-rule py-3 last:border-b-0 sm:flex-row sm:gap-6">
      <dt className="w-full text-xs font-medium uppercase tracking-[0.12em] text-ink-muted sm:w-52 sm:shrink-0 sm:pt-0.5">
        {term}
      </dt>
      <dd className="text-[15px] text-ink">{children}</dd>
    </div>
  );
}

/**
 * A thin rule between sections.
 */
export function Hairline() {
  return <hr className="my-10 border-0 border-t border-rule" />;
}
