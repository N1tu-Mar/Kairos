"use client";

import { useState } from "react";

import type {
  IntakeEvidence,
  IntakeFieldState,
  IntakeKnowledgeClaim,
  IntakeSessionView,
} from "@/lib/types";

interface Props {
  view: IntakeSessionView;
  disabled: boolean;
  onBatchConfirm: () => Promise<unknown>;
  onFieldAction: (fact: IntakeFieldState, action: "confirm" | "correct" | "reject", value?: string) => Promise<boolean>;
  onClaimAction: (claim: IntakeKnowledgeClaim, action: "confirm" | "correct" | "reject", text?: string) => Promise<boolean>;
  onEvidence: (evidence: IntakeEvidence) => Promise<void>;
  onFinish: () => Promise<void>;
}

const REQUIRED_FIELD_COUNT = 11;

function labelFor(value: string): string {
  return value.replaceAll("_", " ");
}

function displayValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(" – ");
  if (value && typeof value === "object") return JSON.stringify(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value ?? "");
}

function EvidenceButtons({ evidence, onEvidence }: { evidence: IntakeEvidence[]; onEvidence: Props["onEvidence"] }) {
  if (!evidence.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {evidence.map((item, index) => (
        <button
          type="button"
          key={`${item.source_id}-${index}`}
          className="text-[10px] text-accent underline underline-offset-2"
          onClick={() => void onEvidence(item)}
        >
          Evidence {item.location ? `· ${item.location.replace(":", " ")}` : ""}
        </button>
      ))}
    </div>
  );
}

export function IntakeMemory({
  view,
  disabled,
  onBatchConfirm,
  onFieldAction,
  onClaimAction,
  onEvidence,
  onFinish,
}: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const facts = Object.values(view.session.fields).filter(
    (fact): fact is IntakeFieldState => Boolean(fact),
  );
  const proposedFacts = facts.filter((fact) => fact.status === "proposed");
  const confirmedFacts = facts.filter((fact) => fact.status === "confirmed");
  const claims = Object.values(view.session.memory.claims);
  const proposedClaims = claims.filter((claim) => claim.status === "proposed");
  const confirmedClaims = claims.filter((claim) => claim.status === "confirmed");
  const proposedCount = proposedFacts.length + proposedClaims.length;
  const completed = Math.max(0, REQUIRED_FIELD_COUNT - view.missing_required.length);
  const progress = Math.round((completed / REQUIRED_FIELD_COUNT) * 100);

  function editor(id: string, current: string, save: () => Promise<boolean>) {
    return editing === id ? (
      <form
        className="mt-2"
        onSubmit={async (event) => {
          event.preventDefault();
          if (await save()) setEditing(null);
        }}
      >
        <label className="sr-only" htmlFor={`edit-${id}`}>Correct captured information</label>
        <textarea
          id={`edit-${id}`}
          autoFocus
          value={editText}
          maxLength={4_000}
          rows={3}
          onChange={(event) => setEditText(event.target.value)}
          className="w-full resize-y rounded-md border border-rule bg-canvas px-2 py-2 text-xs text-ink focus:border-accent focus:outline-none"
        />
        <div className="mt-2 flex gap-2">
          <button type="submit" disabled={!editText.trim() || disabled} className="rounded bg-accent px-2.5 py-1 text-xs text-surface disabled:opacity-40">Save correction</button>
          <button type="button" onClick={() => { setEditing(null); setEditText(current); }} className="text-xs text-ink-muted">Cancel</button>
        </div>
      </form>
    ) : null;
  }

  const actionButtons = (
    id: string,
    current: string,
    confirm: () => Promise<unknown>,
    correct: (text: string) => Promise<boolean>,
    reject: () => Promise<unknown>,
  ) => (
    <>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
        <button type="button" disabled={disabled} className="text-ok hover:underline disabled:opacity-40" onClick={() => void confirm()}>Confirm</button>
        <button type="button" disabled={disabled} className="text-accent hover:underline disabled:opacity-40" onClick={() => { setEditing(id); setEditText(current); }}>Edit</button>
        <button type="button" disabled={disabled} className="text-alert hover:underline disabled:opacity-40" onClick={() => void reject()}>Reject</button>
      </div>
      {editor(id, current, () => correct(editText.trim()))}
    </>
  );

  return (
    <aside className="rounded-xl border border-rule bg-surface p-5 sm:p-6" aria-labelledby="founder-memory-heading">
      <div className="flex items-baseline justify-between gap-4">
        <h3 id="founder-memory-heading" className="font-serif text-lg tracking-tight text-ink">Working memory</h3>
        <span className="font-mono text-xs text-ink-muted">{progress}%</span>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-rule" role="progressbar" aria-label="Required confirmed founder facts" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
        <div className="h-full rounded-full bg-accent" style={{ width: `${progress}%` }} />
      </div>
      <p className="mt-3 text-xs leading-relaxed text-ink-muted">Kairos may infer details, but matching and drafting use only what you confirm.</p>

      <section className="mt-5" aria-labelledby="proposed-memory-heading">
        <div className="flex items-center justify-between gap-3">
          <h4 id="proposed-memory-heading" className="text-[11px] font-medium uppercase tracking-[0.12em] text-warn">Proposed · needs your confirmation · {proposedCount}</h4>
          {view.session.pending_confirmation_batch && proposedCount ? (
            <button type="button" disabled={disabled} onClick={() => void onBatchConfirm()} className="text-xs text-accent underline underline-offset-2 disabled:opacity-40">Confirm all captured facts</button>
          ) : null}
        </div>
        {proposedCount ? (
          <ul className="mt-2 space-y-2">
            {proposedFacts.map((fact) => (
              <li key={fact.field} className="rounded-md border border-rule px-3 py-2">
                <p className="text-[11px] capitalize text-ink-muted">Kairos inferred · {labelFor(fact.field)}</p>
                <p className="mt-0.5 break-words text-sm text-ink">{displayValue(fact.value)}</p>
                <EvidenceButtons evidence={fact.evidence} onEvidence={onEvidence} />
                {actionButtons(`field-${fact.field}`, displayValue(fact.value), () => onFieldAction(fact, "confirm"), (value) => onFieldAction(fact, "correct", value), () => onFieldAction(fact, "reject"))}
              </li>
            ))}
            {proposedClaims.map((claim) => (
              <li key={claim.claim_id} className="rounded-md border border-rule px-3 py-2">
                <p className="text-[11px] capitalize text-ink-muted">Kairos inferred · {labelFor(claim.category)}</p>
                <p className="mt-0.5 break-words text-sm text-ink">{claim.text}</p>
                <EvidenceButtons evidence={claim.evidence} onEvidence={onEvidence} />
                {actionButtons(`claim-${claim.claim_id}`, claim.text, () => onClaimAction(claim, "confirm"), (text) => onClaimAction(claim, "correct", text), () => onClaimAction(claim, "reject"))}
              </li>
            ))}
          </ul>
        ) : <p className="mt-2 text-xs text-ink-muted">Nothing is waiting for review.</p>}
      </section>

      <details className="mt-5 border-t border-rule pt-4">
        <summary className="cursor-pointer text-[11px] font-medium uppercase tracking-[0.12em] text-ok">Confirmed · {confirmedFacts.length + confirmedClaims.length}</summary>
        <ul className="mt-2 space-y-2 text-xs text-ink-soft">
          {confirmedFacts.map((fact) => <li key={fact.field}><span className="capitalize text-ink-muted">{labelFor(fact.field)}:</span> {displayValue(fact.value)}</li>)}
          {confirmedClaims.map((claim) => <li key={claim.claim_id}><span className="capitalize text-ink-muted">{labelFor(claim.category)}:</span> {claim.text}</li>)}
        </ul>
      </details>

      <details className="mt-4 border-t border-rule pt-4" open={view.missing_required.length > 0}>
        <summary className="cursor-pointer text-[11px] font-medium uppercase tracking-[0.12em] text-ink-muted">Still needed · {view.missing_required.length}</summary>
        <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs capitalize text-ink-muted">
          {view.missing_required.map((field) => <li key={field}>{labelFor(field)}</li>)}
        </ul>
      </details>

      <button type="button" disabled={disabled || !view.ready_to_complete || view.session.status !== "active"} onClick={() => void onFinish()} className="mt-5 w-full rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-surface disabled:cursor-not-allowed disabled:opacity-40">
        {view.session.status === "completed" ? "Founder memory completed" : "Finish founder profile"}
      </button>
      {!view.ready_to_complete && view.session.status === "active" ? <p className="mt-2 text-center text-[11px] text-ink-muted">Finish unlocks when every required fact is confirmed.</p> : null}
    </aside>
  );
}
