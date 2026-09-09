"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { IntakeDocuments } from "@/components/intake-documents";
import { IntakeMemory } from "@/components/intake-memory";
import type {
  FounderProfile,
  IntakeEvidence,
  IntakeFieldState,
  IntakeKnowledgeClaim,
  IntakeMessage,
  IntakeSessionView,
} from "@/lib/types";

type Phase = "loading" | "ready" | "sending" | "error";

const INTRO =
  "Tell me what you’re building in your own words. Share as much context as you have—I’ll pull out the useful facts and ask one follow-up at a time.";
const COMPOSER_ID = "intake-message";

function newClientId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `web-${crypto.randomUUID()}`;
  }
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

async function responseError(response: Response, fallback: string): Promise<string> {
  const body = (await response.json().catch(() => null)) as { error?: unknown } | null;
  return typeof body?.error === "string" ? body.error : fallback;
}

function correctionValue(field: string, value: string): unknown {
  const trimmed = value.trim();
  if (["team_size", "max_application_hours"].includes(field)) {
    const number = Number(trimmed);
    if (!Number.isFinite(number)) throw new Error("Enter a number for this fact.");
    return number;
  }
  if (["equity_ok", "has_faculty_advisor"].includes(field)) {
    if (/^(yes|true)$/i.test(trimmed)) return true;
    if (/^(no|false)$/i.test(trimmed)) return false;
    throw new Error("Enter yes or no for this fact.");
  }
  if (field === "funding_range") {
    const numbers = trimmed
      .match(/\d[\d,]*/g)
      ?.map((item) => Number(item.replaceAll(",", "")));
    if (!numbers || numbers.length !== 2) {
      throw new Error("Enter a minimum and maximum funding amount.");
    }
    return numbers;
  }
  if (field === "geographies") {
    const items = trimmed.split(",").map((item) => item.trim()).filter(Boolean);
    if (!items.length) throw new Error("Enter at least one geography.");
    return items;
  }
  return trimmed;
}

function ChatMessage({ message }: { message: IntakeMessage }) {
  const founder = message.role === "founder";
  return (
    <div
      id={`intake-message-${message.message_id}`}
      tabIndex={-1}
      className={`${founder ? "flex justify-end" : "flex justify-start"} scroll-mt-4 outline-none focus-visible:ring-2 focus-visible:ring-accent`}
    >
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

export function IntakeChat({}: { profile: FounderProfile | null }) {
  const [view, setView] = useState<IntakeSessionView | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [draft, setDraft] = useState("");
  const [optimisticText, setOptimisticText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [retryable, setRetryable] = useState(false);
  const [evidence, setEvidence] = useState<{ label: string; excerpt: string } | null>(null);
  const retry = useRef<{ id: string; text: string } | null>(null);
  const inFlight = useRef(false);
  const endRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const loadSession = useCallback(async (quiet = false) => {
    if (!quiet) setPhase("loading");
    try {
      const response = await fetch("/api/intake", { method: "POST" });
      if (!response.ok) {
        throw new Error(await responseError(response, "The founder interview could not be loaded."));
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
        setError(caught instanceof Error ? caught.message : "The founder interview could not be loaded.");
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

  const messages = useMemo(() => view?.messages ?? [], [view?.messages]);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [messages, optimisticText]);

  async function sendMessage() {
    const text = draft.trim();
    if (!view || !text || inFlight.current || view.session.status !== "active") return;
    inFlight.current = true;
    setPhase("sending");
    setError(null);
    setNotice(null);
    setRetryable(false);
    setOptimisticText(text);
    setDraft("");
    const pending = retry.current?.text === text
      ? retry.current
      : { id: newClientId(), text };
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
      if (!response.ok) throw new Error(await responseError(response, "The message could not be sent."));
      setView((await response.json()) as IntakeSessionView);
      retry.current = null;
      setRetryable(false);
      setPhase("ready");
    } catch (caught) {
      setDraft(text);
      setError(caught instanceof Error ? caught.message : "The message could not be sent.");
      setRetryable(true);
      setPhase("error");
      await loadSession(true);
    } finally {
      setOptimisticText(null);
      inFlight.current = false;
    }
  }

  async function mutate(url: string, method: "POST" | "PATCH", body: unknown, success: string) {
    if (!view || inFlight.current) return false;
    inFlight.current = true;
    setPhase("sending");
    setError(null);
    setNotice(null);
    setRetryable(false);
    try {
      const response = await fetch(url, {
        method,
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error(await responseError(response, "Founder memory could not be updated."));
      }
      setView((await response.json()) as IntakeSessionView);
      setNotice(success);
      setPhase("ready");
      window.setTimeout(() => composerRef.current?.focus(), 0);
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Founder memory could not be updated.");
      setPhase("error");
      await loadSession(true);
      return false;
    } finally {
      inFlight.current = false;
    }
  }

  async function fieldAction(
    fact: IntakeFieldState,
    action: "confirm" | "correct" | "reject",
    value?: string,
  ) {
    if (!view) return false;
    let corrected: unknown;
    try {
      corrected = action === "correct" ? correctionValue(fact.field, value ?? "") : undefined;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That correction is invalid.");
      return false;
    }
    return mutate(
      `/api/intake/${encodeURIComponent(view.session.session_id)}/fields/${encodeURIComponent(fact.field)}`,
      "PATCH",
      {
        action,
        expected_revision: view.session.revision,
        ...(action === "correct" ? { value: corrected, client_action_id: newClientId() } : {}),
      },
      action === "reject" ? "The inferred fact was rejected." : "Confirmed founder memory was updated.",
    );
  }

  async function claimAction(
    claim: IntakeKnowledgeClaim,
    action: "confirm" | "correct" | "reject",
    text?: string,
  ) {
    if (!view) return false;
    return mutate(
      `/api/intake/${encodeURIComponent(view.session.session_id)}/claims/${encodeURIComponent(claim.claim_id)}`,
      "PATCH",
      {
        action,
        expected_revision: view.session.revision,
        client_action_id: newClientId(),
        ...(action === "correct" ? { text, category: claim.category } : {}),
      },
      action === "reject" ? "The inferred claim was rejected." : "Confirmed founder memory was updated.",
    );
  }

  async function showEvidence(item: IntakeEvidence) {
    if (!view) return;
    if (item.source_type === "message") {
      const target = document.getElementById(`intake-message-${item.source_id}`);
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
      target?.focus({ preventScroll: true });
    }
    try {
      const response = await fetch(
        `/api/intake/${encodeURIComponent(view.session.session_id)}/evidence/${encodeURIComponent(item.source_id)}`,
      );
      if (!response.ok) throw new Error(await responseError(response, "Evidence could not be loaded."));
      const loaded = (await response.json()) as { location: string | null; excerpt: string };
      setEvidence({
        label: loaded.location?.replace(":", " ") ?? "Conversation message",
        excerpt: loaded.excerpt,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Evidence could not be loaded.");
    }
  }

  async function finish() {
    if (!view || !view.ready_to_complete || inFlight.current) return;
    inFlight.current = true;
    setPhase("sending");
    setError(null);
    try {
      const response = await fetch(
        `/api/intake/${encodeURIComponent(view.session.session_id)}/complete`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ expected_revision: view.session.revision }),
        },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "The founder profile could not be completed."));
      }
      setView({ ...view, session: { ...view.session, status: "completed" } });
      setNotice("Your confirmed founder memory is ready for matching and drafting.");
      setPhase("ready");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The founder profile could not be completed.");
      setPhase("error");
    } finally {
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
        <p className="text-sm text-alert" role="alert">{error ?? "The founder interview could not be loaded."}</p>
        <button type="button" onClick={() => void loadSession()} className="mt-3 rounded-md border border-rule px-4 py-2 text-sm text-ink hover:border-accent">Try again</button>
      </div>
    );
  }

  const disabled = phase === "sending" || view.turn_pending;
  const sessionPath = `/api/intake/${encodeURIComponent(view.session.session_id)}`;

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.45fr)_minmax(18rem,0.75fr)]">
      <section className="flex min-h-[38rem] flex-col overflow-hidden rounded-xl border border-rule bg-surface">
        <header className="flex items-center justify-between border-b border-rule px-5 py-4">
          <div>
            <h3 className="font-serif text-lg tracking-tight text-ink">Founder interview</h3>
            <p className="text-xs text-ink-muted">Private conversation · confirmed memory only</p>
          </div>
          <span className="flex items-center gap-2 text-xs text-ink-muted">
            <span className="h-2 w-2 rounded-full bg-ok" aria-hidden="true" />Session saved
          </span>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-6" aria-label="Founder interview transcript">
          {!messages.length ? (
            <div className="flex justify-start">
              <div className="max-w-[90%] rounded-2xl rounded-bl-sm border border-rule bg-surface px-4 py-3 text-sm leading-relaxed text-ink">
                <span className="mb-1 block text-[10px] font-medium uppercase tracking-[0.12em] text-ink-muted">Kairos</span>
                <p>{INTRO}</p>
              </div>
            </div>
          ) : null}
          {messages.map((message) => <ChatMessage key={message.message_id} message={message} />)}
          {optimisticText ? (
            <div className="flex justify-end"><div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-4 py-3 text-sm text-surface opacity-80">{optimisticText}</div></div>
          ) : null}
          {disabled ? (
            <div className="flex items-center gap-2 text-xs text-ink-muted" role="status">
              <span className="flex gap-1" aria-hidden="true"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" /><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent [animation-delay:150ms]" /><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent [animation-delay:300ms]" /></span>
              Kairos is thinking…
            </div>
          ) : null}
          <div ref={endRef} />
        </div>

        <div className="border-t border-rule p-4 sm:p-5">
          <form onSubmit={(event) => { event.preventDefault(); void sendMessage(); }}>
            <label htmlFor={COMPOSER_ID} className="sr-only">Message Kairos about your startup</label>
            <textarea
              ref={composerRef}
              id={COMPOSER_ID}
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                if (retry.current?.text !== event.target.value.trim()) {
                  retry.current = null;
                  setRetryable(false);
                }
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
              <p className="text-[11px] text-ink-muted">Enter to send · Shift+Enter for a new line</p>
              <button type="submit" disabled={disabled || !draft.trim() || view.session.status !== "active"} className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-surface hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40">{phase === "sending" ? "Sending…" : "Send"}</button>
            </div>
          </form>
          <div aria-live="polite" aria-atomic="true">
            {notice ? <p className="mt-3 text-sm text-ok" role="status">{notice}</p> : null}
            {error ? <p className="mt-3 text-sm text-alert" role="alert">{error}{retryable ? " Your message is safe to retry." : ""}</p> : null}
          </div>
        </div>
      </section>

      <div className="space-y-5 lg:max-h-[48rem] lg:overflow-y-auto lg:pr-1">
        <IntakeMemory
          view={view}
          disabled={disabled}
          onBatchConfirm={() => {
            const batch = view.session.pending_confirmation_batch;
            if (!batch) return Promise.resolve(false);
            return mutate(
              `${sessionPath}/proposal-batches/${encodeURIComponent(batch.batch_id)}/confirm`,
              "POST",
              { expected_revision: view.session.revision },
              "All displayed proposals were added to confirmed founder memory.",
            );
          }}
          onFieldAction={fieldAction}
          onClaimAction={claimAction}
          onEvidence={showEvidence}
          onFinish={finish}
        />
        <div className="rounded-xl border border-rule bg-surface p-5 sm:p-6">
          <IntakeDocuments
            sessionId={view.session.session_id}
            documents={view.documents}
            disabled={disabled || view.session.status !== "active"}
            onChanged={async () => { await loadSession(true); }}
            onNotice={(message) => { setNotice(message); setError(null); }}
            onError={(message) => {
              setError(message || null);
              setRetryable(false);
              if (message) setNotice(null);
            }}
          />
        </div>
        {evidence ? (
          <section className="rounded-xl border border-accent/30 bg-canvas p-4" aria-live="polite" aria-label="Supporting evidence">
            <div className="flex justify-between gap-3">
              <h4 className="text-xs font-medium capitalize text-ink">{evidence.label}</h4>
              <button type="button" onClick={() => setEvidence(null)} className="text-xs text-ink-muted">Close</button>
            </div>
            <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-relaxed text-ink-soft">{evidence.excerpt}</p>
          </section>
        ) : null}
      </div>
    </div>
  );
}

export function IntakeSection({ profile }: { profile: FounderProfile | null }) {
  const [open, setOpen] = useState(profile === null);
  if (!open) {
    return (
      <div className="rounded-xl border border-rule bg-surface p-5 sm:p-6">
        <p className="text-sm leading-relaxed text-ink-soft">Kairos is matching against {profile?.institution}, {profile?.stage} stage, team of {profile?.team_size}. Open the interview to add context naturally; the agent will only ask for what is still missing.</p>
        <button type="button" onClick={() => setOpen(true)} className="mt-3 rounded-md border border-rule px-4 py-2 text-sm text-ink-soft hover:border-accent hover:text-ink">Continue founder interview</button>
      </div>
    );
  }
  return <IntakeChat profile={profile} />;
}
