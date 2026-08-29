import {
  AuditBadge,
  Badge,
  DemoBadge,
  FieldStatusBadge,
} from "@/components/badges";
import { FIELD_STATUS_LABELS, isDemo } from "@/lib/format";
import { FIELD_STATUSES } from "@/lib/types";
import type {
  DraftCounts,
  DraftField,
  GateResult,
  SourceSpan,
} from "@/lib/types";

/** Field counts, computed in Python and displayed verbatim. */
export function DraftCountsSummary({ counts }: { counts: DraftCounts }) {
  const total = FIELD_STATUSES.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
  const needsYou = counts.NEEDS_FOUNDER ?? 0;

  return (
    <div className="space-y-4">
      <p className="font-serif text-xl leading-snug tracking-tight text-ink">
        {total} {total === 1 ? "question" : "questions"} on this form.{" "}
        {needsYou === 0
          ? "None of them need you."
          : `${needsYou} ${needsYou === 1 ? "needs" : "need"} you.`}
      </p>

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-rule bg-rule sm:grid-cols-4">
        {FIELD_STATUSES.map((status) => (
          <div key={status} className="bg-surface px-4 py-4">
            <dt className="text-[11px] font-medium uppercase tracking-[0.12em] text-ink-muted">
              {FIELD_STATUS_LABELS[status] ?? status}
            </dt>
            <dd
              className={`mt-1 font-serif text-2xl tabular-nums ${
                status === "NEEDS_FOUNDER" && (counts[status] ?? 0) > 0
                  ? "text-alert"
                  : "text-ink"
              }`}
            >
              {counts[status] ?? 0}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Why the ship gate refused. Fail-closed by design, so this is the normal case. */
export function GateOutcome({ gate }: { gate: GateResult | null }) {
  if (!gate) {
    return (
      <p className="text-sm leading-relaxed text-ink-muted">
        The ship gate has not run on this draft yet, so it is not cleared for
        review.
      </p>
    );
  }

  const blocking = gate.violations.filter((v) => v.severity === "BLOCK");
  const corrections = gate.violations.filter(
    (v) => v.severity === "FORCED_NEEDS_FOUNDER",
  );

  return (
    <div className="space-y-4">
      {gate.passed ? (
        <div className="rounded-md border border-ok/40 bg-ok-soft px-4 py-3">
          <p className="text-sm font-medium text-ink">
            The draft cleared every gate check
          </p>
          <p className="mt-1 text-sm leading-relaxed text-ink-soft">
            Every claim in it traces back to something you actually told Kairos.
            It is ready for you to read — not ready to send.
          </p>
        </div>
      ) : (
        <div className="rounded-md border border-alert/40 bg-alert-soft px-4 py-3">
          <p className="text-sm font-medium text-ink">
            Blocked at{" "}
            <span className="font-mono text-xs">
              {gate.failed_check ?? "an unnamed check"}
            </span>
          </p>
          <p className="mt-1 text-sm leading-relaxed text-ink-soft">
            The gate stops at the first failure and refuses the draft. An
            exception inside the gate is never read as a pass.
          </p>
        </div>
      )}

      {blocking.length > 0 ? (
        <ul className="divide-y divide-rule rounded-lg border border-rule bg-surface">
          {blocking.map((violation, index) => (
            <li key={`${violation.check}-${index}`} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs font-semibold text-alert">
                  {violation.check}
                </span>
                {violation.field_id ? (
                  <span className="font-mono text-xs text-ink-muted">
                    {violation.field_id}
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-sm leading-relaxed text-ink-soft">
                {violation.detail}
              </p>
            </li>
          ))}
        </ul>
      ) : null}

      {corrections.length > 0 ? (
        <details className="rounded-lg border border-rule bg-surface px-4 py-3">
          <summary className="cursor-pointer text-sm text-ink">
            {corrections.length} field
            {corrections.length === 1 ? " was" : "s were"} handed back to you
            rather than failed
          </summary>
          <ul className="mt-3 space-y-2.5">
            {corrections.map((violation, index) => (
              <li key={`${violation.check}-${index}`} className="text-sm text-ink-soft">
                <span className="font-mono text-xs text-warn">
                  {violation.check}
                </span>
                {violation.field_id ? (
                  <span className="ml-2 font-mono text-xs text-ink-muted">
                    {violation.field_id}
                  </span>
                ) : null}
                <span className="mt-0.5 block">{violation.detail}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {gate.checks_run.length > 0 ? (
        <p className="text-xs leading-relaxed text-ink-muted">
          Checks run, in order:{" "}
          <span className="font-mono">{gate.checks_run.join(" → ")}</span>
        </p>
      ) : null}
    </div>
  );
}

/**
 * The quoted spans behind one generated answer.
 *
 * Renders nothing when there are none — which is not a silent omission,
 * because a GENERATED field with empty provenance never reaches this
 * component: the ship gate blocks the draft first.
 */
function Provenance({ spans }: { spans: SourceSpan[] }) {
  if (spans.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-ink-muted">
        Where this came from
      </p>
      <ul className="mt-1.5 space-y-2">
        {spans.map((span, index) => (
          <li
            key={`${span.chunk_id}-${index}`}
            className="border-l-2 border-rule-strong pl-3"
          >
            <p className="text-sm italic leading-relaxed text-ink-soft">
              &ldquo;{span.text}&rdquo;
            </p>
            <p className="mt-0.5 font-mono text-[11px] text-ink-muted">
              {span.source}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * One form question, its answer, where the answer came from, and its audit verdict.
 *
 * The status badge and the provenance block are the point: a reader must be
 * able to tell at a glance whether a model wrote this text and what it was
 * based on.
 */
export function DraftFieldCard({ field }: { field: DraftField }) {
  const needsFounder = field.status === "NEEDS_FOUNDER";
  const answered = (field.answer ?? "").trim().length > 0;

  return (
    <article
      className={
        needsFounder
          ? "rounded-lg border-l-4 border-l-alert border-y border-r border-rule bg-alert-soft/40 p-5"
          : "rounded-lg border border-rule bg-surface p-5"
      }
    >
      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        <FieldStatusBadge status={field.status} />
        {field.audit_verdict ? <AuditBadge verdict={field.audit_verdict} /> : null}
        {field.reused_from ? (
          <Badge
            tone="info"
            title={`Reused from a previous answer: ${field.reused_from}`}
          >
            Answered before
          </Badge>
        ) : null}
      </div>

      <h3 className="text-[15px] font-medium leading-snug text-ink">
        {field.question}
      </h3>

      {needsFounder ? (
        <p className="mt-3 text-[15px] leading-relaxed text-ink-soft">
          Kairos left this blank on purpose. Nothing in your knowledge base
          answers it, and filling it in would mean inventing something.
        </p>
      ) : answered ? (
        <p className="mt-3 whitespace-pre-line text-[15px] leading-relaxed text-ink">
          {field.answer}
        </p>
      ) : (
        <p className="mt-3 text-[15px] italic leading-relaxed text-ink-muted">
          No answer recorded.
        </p>
      )}

      <Provenance spans={field.provenance} />

      {field.audit_note ? (
        <p className="mt-3 text-sm leading-relaxed text-ink-muted">
          Auditor: {field.audit_note}
        </p>
      ) : null}

      {field.model_id ? (
        <p className="mt-3 border-t border-rule pt-2.5 font-mono text-[11px] text-ink-muted">
          {field.model_id}
          {field.prompt_version ? ` · prompt ${field.prompt_version}` : ""}
        </p>
      ) : null}
    </article>
  );
}

/**
 * The form's name and source, with the partial-transcription warning when the form is incomplete.
 */
export function DraftFormName({ name }: { name: string }) {
  if (!name) return null;
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span>{name}</span>
      {isDemo(name) ? <DemoBadge /> : null}
    </span>
  );
}
