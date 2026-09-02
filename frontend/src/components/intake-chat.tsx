"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  FounderProfile,
  IntakeFieldState,
  IntakeMessage,
  IntakeSessionView,
} from "@/lib/types";

type Phase = "loading" | "ready" | "sending" | "error";

const REQUIRED_FIELD_COUNT = 11;
const INTRO =
  "Tell me what you’re building in your own words. Share as much context as you have—I’ll pull out the useful facts and ask one follow-up at a time.";

function newClientMessageId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `web-${crypto.randomUUID()}`;
  }
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

async function responseError(response: Response, fallback: string): Promise<string> {
  const body = (await response.json().catch(() => null)) as
    | { error?: unknown }
    | null;
  return typeof body?.error === "string" ? body.error : fallback;
}

function labelFor(field: string): string {
  return field.replaceAll("_", " ");
}

function displayValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(" – ");
  if (value && typeof value === "object") return JSON.stringify(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value ?? "");
}

function ChatMessage({ message }: { message: IntakeMessage }) {
  const founder = message.role === "founder";
  return (
    <div className={founder ? "flex justify-end" : "flex justify-start"}>
      <div
        className={
          founder
            ? "max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-4 py-3 text-sm leading-relaxed text-surface"
            : "max-w-[90%] rounded-2xl rounded-bl-sm border border-rule bg-surface px-4 py-3 text-sm leading-relaxed text-ink"
        }
      >
        <span className="mb-1 block text-[10px] font-medium uppercase tracking-[0.12em] opacity-70">
          {founder ? "You" : "Kairos"}
        </span>
        <p className="whitespace-pre-wrap break-words">{message.text}</p>
      </div>
    </div>
  );
}

function FactSummary({ view }: { view: IntakeSessionView }) {
  const facts = Object.values(view.session.fields).filter(
    (fact): fact is IntakeFieldState => Boolean(fact),
  );
  const proposed = facts.filter((fact) => fact.status === "proposed");
  const confirmed = facts.filter((fact) => fact.status === "confirmed");
  const completed = Math.max(0, REQUIRED_FIELD_COUNT - view.missing_required.length);
  const progress = Math.round((completed / REQUIRED_FIELD_COUNT) * 100);

  return (
    <aside className="rounded-xl border border-rule bg-surface p-5 sm:p-6">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="font-serif text-lg tracking-tight text-ink">Founder context</h3>
        <span className="font-mono text-xs text-ink-muted">{progress}%</span>
      </div>
      <div
        className="mt-3 h-1.5 overflow-hidden rounded-full bg-rule"
        role="progressbar"
        aria-label="Required founder facts captured"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
      >
        <div className="h-full rounded-full bg-accent" style={{ width: `${progress}%` }} />
      </div>
      <p className="mt-3 text-xs leading-relaxed text-ink-muted">
        The assistant can propose facts. Eligibility-sensitive facts are not saved
        to your profile until you confirm them.
      </p>

      {proposed.length > 0 ? (
        <div className="mt-5">
          <h4 className="text-[11px] font-medium uppercase tracking-[0.12em] text-warn">
            Needs your confirmation · {proposed.length}
          </h4>
          <dl className="mt-2 space-y-2">
            {proposed.map((fact) => (
              <div key={fact.field} className="rounded-md border border-rule px-3 py-2">
                <dt className="text-[11px] capitalize text-ink-muted">
                  {labelFor(fact.field)}
                </dt>
                <dd className="mt-0.5 break-words text-sm text-ink">
                  {displayValue(fact.value)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}

      <div className="mt-5 grid grid-cols-2 gap-3">
        <div className="rounded-md bg-canvas px-3 py-2">
          <span className="block font-serif text-xl text-ink">{confirmed.length}</span>
          <span className="text-[11px] uppercase tracking-[0.1em] text-ink-muted">
            Confirmed
          </span>
        </div>
        <div className="rounded-md bg-canvas px-3 py-2">
          <span className="block font-serif text-xl text-ink">
            {view.missing_required.length}
          </span>
          <span className="text-[11px] uppercase tracking-[0.1em] text-ink-muted">
            Still needed
          </span>
        </div>
      </div>
    </aside>
  );
}

export function IntakeChat({ founderId }: { profile: FounderProfile | null; founderId: string }) {
  const [view, setView] = useState<IntakeSessionView | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [draft, setDraft] = useState("");
  const [optimisticText, setOptimisticText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const retry = useRef<{ id: string; text: string } | null>(null);
  const inFlight = useRef(false);
  const endRef = useRef<HTMLDivElement>(null);

  const loadSession = useCallback(async (quiet = false) => {
    if (!quiet) setPhase("loading");
    try {
      const response = await fetch("/api/intake", { method: "POST" });
      if (!response.ok) {
        throw new Error(
          await responseError(response, "The founder interview could not be loaded."),
        );
      }
      const loaded = (await response.json()) as IntakeSessionView;
      setView(loaded);
      if (!quiet) {
        setError(null);
        setPhase("ready");
      }
      return loaded;
    } catch (caught) {
      if (!quiet) {
        setError(
          caught instanceof Error
            ? caught.message
            : "The founder interview could not be loaded.",
        );
        setPhase("error");
      }
      return null;
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSession(), 0);
    return () => window.clearTimeout(timer);
  }, [loadSession]);

  useEffect(() => {
    if (!view?.turn_pending || phase === "sending") return;
    const timer = window.setTimeout(() => void loadSession(true), 1_500);
    return () => window.clearTimeout(timer);
  }, [loadSession, phase, view?.turn_pending]);

  const visibleMessages = useMemo(() => view?.messages ?? [], [view?.messages]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [optimisticText, visibleMessages]);

  async function sendMessage() {
    const text = draft.trim();
    if (!view || !text || inFlight.current || view.session.status !== "active") return;
    inFlight.current = true;
    setPhase("sending");
    setError(null);
    setOptimisticText(text);
    setDraft("");
    const pending =
      retry.current?.text === text
        ? retry.current
        : { id: newClientMessageId(), text };
    retry.current = pending;

    try {
      const response = await fetch(
        `/api/intake/${encodeURIComponent(view.session.session_id)}/messages`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            text,
            client_message_id: pending.id,
            expected_revision: view.session.revision,
          }),
        },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "The message could not be sent."));
      }
      setView((await response.json()) as IntakeSessionView);
      retry.current = null;
      setPhase("ready");
    } catch (caught) {
      setDraft(text);
      setError(
        caught instanceof Error ? caught.message : "The message could not be sent.",
      );
      setPhase("error");
      // Re-sync the revision without changing the idempotency key. Retrying
      // the same text can never create a second model charge.
      await loadSession(true);
    } finally {
      setOptimisticText(null);
      inFlight.current = false;
    }
  }

  if (phase === "loading" && !view) {
    return (
      <div className="rounded-xl border border-rule bg-surface p-6" role="status">
        <p className="text-sm text-ink-muted">Opening your founder interview…</p>
      </div>
    );
  }

  if (!view) {
    return (
      <div className="rounded-xl border border-alert/40 bg-surface p-6">
        <p className="text-sm text-alert" role="alert">
          {error ?? "The founder interview could not be loaded."}
        </p>
        <button
          type="button"
          onClick={() => void loadSession()}
          className="mt-3 rounded-md border border-rule px-4 py-2 text-sm text-ink hover:border-accent"
        >
          Try again
        </button>
      </div>
    );
  }

  const disabled = phase === "sending" || view.turn_pending;

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.45fr)_minmax(18rem,0.75fr)]">
      <section className="flex min-h-[34rem] flex-col overflow-hidden rounded-xl border border-rule bg-surface">
        <header className="flex items-center justify-between border-b border-rule px-5 py-4">
          <div>
            <h3 className="font-serif text-lg tracking-tight text-ink">Founder interview</h3>
            <p className="text-xs text-ink-muted">Powered by the private Kairos agent</p>
          </div>
          <span className="flex items-center gap-2 text-xs text-ink-muted">
            <span className="h-2 w-2 rounded-full bg-ok" aria-hidden="true" />
            Session saved
          </span>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-6">
          {visibleMessages.length === 0 ? (
            <div className="flex justify-start">
              <div className="max-w-[90%] rounded-2xl rounded-bl-sm border border-rule bg-surface px-4 py-3 text-sm leading-relaxed text-ink">
                <span className="mb-1 block text-[10px] font-medium uppercase tracking-[0.12em] text-ink-muted">
                  Kairos
                </span>
                <p>{INTRO}</p>
              </div>
            </div>
          ) : null}
          {visibleMessages.map((message) => (
            <ChatMessage key={message.message_id} message={message} />
          ))}
          {optimisticText ? (
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-4 py-3 text-sm text-surface opacity-80">
                {optimisticText}
              </div>
            </div>
          ) : null}
          {disabled ? (
            <div className="flex items-center gap-2 text-xs text-ink-muted" role="status">
              <span className="flex gap-1" aria-hidden="true">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent [animation-delay:150ms]" />
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent [animation-delay:300ms]" />
              </span>
              Kairos is thinking…
            </div>
          ) : null}
          <div ref={endRef} />
        </div>

        <div className="border-t border-rule p-4 sm:p-5">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void sendMessage();
            }}
          >
            <label htmlFor={`intake-message-${founderId}`} className="sr-only">
              Message Kairos about your startup
            </label>
            <textarea
              id={`intake-message-${founderId}`}
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                if (retry.current?.text !== event.target.value.trim()) retry.current = null;
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void sendMessage();
                }
              }}
              maxLength={8_000}
              rows={3}
              disabled={disabled || view.session.status !== "active"}
              placeholder="Describe your startup, paste a short brief, or answer Kairos…"
              className="w-full resize-none rounded-lg border border-rule bg-canvas px-4 py-3 text-sm leading-relaxed text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none disabled:opacity-60"
            />
            <div className="mt-2 flex items-center justify-between gap-4">
              <p className="text-[11px] text-ink-muted">
                Enter to send · Shift+Enter for a new line
              </p>
              <button
                type="submit"
                disabled={disabled || !draft.trim() || view.session.status !== "active"}
                className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-surface hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {phase === "sending" ? "Sending…" : "Send"}
              </button>
            </div>
          </form>
          <div aria-live="polite" aria-atomic="true">
            {error ? (
              <p className="mt-3 text-sm text-alert" role="alert">
                {error} Your message is safe to retry.
              </p>
            ) : null}
          </div>
        </div>
      </section>

      <FactSummary view={view} />
    </div>
  );
}

export function IntakeSection({
  profile,
  founderId,
}: {
  profile: FounderProfile | null;
  founderId: string;
}) {
  const [open, setOpen] = useState(profile === null);

  if (!open) {
    return (
      <div className="rounded-xl border border-rule bg-surface p-5 sm:p-6">
        <p className="text-sm leading-relaxed text-ink-soft">
          Kairos is matching against {profile?.institution}, {profile?.stage} stage,
          team of {profile?.team_size}. Open the interview to add context naturally;
          the agent will only ask for what is still missing.
        </p>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="mt-3 rounded-md border border-rule px-4 py-2 text-sm text-ink-soft hover:border-accent hover:text-ink"
        >
          Continue founder interview
        </button>
      </div>
    );
  }

  return <IntakeChat profile={profile} founderId={founderId} />;
}
