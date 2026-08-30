"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import type {
  DegreeLevel,
  EntityType,
  FounderProfile,
  KnowledgeChunk,
  Stage,
} from "@/lib/types";

/**
 * Conversational intake for the facts Kairos needs before it can run.
 *
 * Two things made this necessary. A founder had no way to say what they are
 * building or how far along they are: `traction` and `knowledge_base` were on
 * the model and reachable through `PUT /founders/{id}`, but no screen wrote
 * either one. And a founder arriving with no profile at all had nowhere to
 * start, which meant the product could not be handed to a stranger.
 *
 * ## Why a script and not a model
 *
 * Every question here is asked by Python-free, model-free code, and every
 * answer is parsed by a `parse` function on its own step. No model call, no
 * key, no network beyond the single `PUT` at the end. That is not a shortcut
 * around a model; it is the same split the rest of the system runs on. The
 * eligibility filter compares structured facts, so structured facts are what
 * intake must produce, and a scripted question that returns
 * `{ok: false, message}` on bad input is a stricter guarantee of that than any
 * extraction prompt. It also means intake works with no credentials
 * configured, which is exactly the state a new user is in.
 *
 * ## Where free text is allowed to go
 *
 * Exactly one place. "What are you building" and anything else prose-shaped
 * becomes a `KnowledgeChunk` tagged `source: "onboarding_chat"` and lands in
 * `knowledge_base`, the closed world the Drafter may quote from. It never
 * reaches an eligibility field. That boundary is the defense against a
 * funding page talking the filter out of its answer, and moving intake to a
 * conversation must not soften it.
 */

// ── The script ───────────────────────────────────────────────────────────────

/** What a step does with the text the founder typed. */
type ParseResult =
  | { ok: true; value: unknown; echo: string }
  | { ok: false; message: string };

interface Step {
  /** Stable key. Also the field it writes, where the two coincide. */
  id: string;
  /** What Kairos asks. */
  question: string;
  /** Shown under the question. Examples and units go here, not in the question. */
  hint?: string;
  /** Offered as buttons. The founder may still type instead. */
  choices?: string[];
  /** Optional steps accept "skip" and record nothing. */
  skippable?: boolean;
  /** Validate and convert. The `echo` is what the transcript shows back. */
  parse: (raw: string) => ParseResult;
}

/** Whole number at or above `min`, for team size, hours and counts. */
function wholeNumber(min: number, label: string) {
  return (raw: string): ParseResult => {
    const cleaned = raw.replace(/[,$\s]/g, "");
    const value = Number(cleaned);
    if (!Number.isFinite(value) || !Number.isInteger(value) || value < min) {
      return {
        ok: false,
        message: `${label} needs to be a whole number of at least ${min}. Try again.`,
      };
    }
    return { ok: true, value, echo: String(value) };
  };
}

/** One of a fixed set, matched case-insensitively and space-or-underscore agnostic. */
function oneOf<T extends string>(options: readonly T[]) {
  return (raw: string): ParseResult => {
    const needle = raw.trim().toLowerCase().replace(/[\s-]+/g, "_");
    const hit = options.find((option) => option === needle);
    if (!hit) {
      return { ok: false, message: `Pick one of: ${options.join(", ")}.` };
    }
    return { ok: true, value: hit, echo: hit };
  };
}

/** Yes or no, in the several shapes people actually type it. */
function yesNo(raw: string): ParseResult {
  const needle = raw.trim().toLowerCase();
  if (["y", "yes", "yeah", "yep", "true", "ok"].includes(needle)) {
    return { ok: true, value: true, echo: "yes" };
  }
  if (["n", "no", "nope", "false"].includes(needle)) {
    return { ok: true, value: false, echo: "no" };
  }
  return { ok: false, message: "Yes or no." };
}

/** Non-empty text, capped at the length the backend model accepts. */
function text(max: number, label: string) {
  return (raw: string): ParseResult => {
    const value = raw.trim();
    if (!value) return { ok: false, message: `${label} cannot be empty.` };
    if (value.length > max) {
      return { ok: false, message: `${label} has to be under ${max} characters.` };
    }
    return { ok: true, value, echo: value };
  };
}

const DEGREE_CHOICES = ["undergrad", "masters", "phd", "postdoc"] as const;
const ENTITY_CHOICES = ["none", "llc", "c_corp", "s_corp", "nonprofit"] as const;
const STAGE_CHOICES = ["idea", "prototype", "mvp", "pilot", "revenue"] as const;

/**
 * The questions, in order.
 *
 * Ordered so the easy identity questions come first and the ones that need a
 * founder to think — funding range, hours they will spend — come once they are
 * already several answers in. The eligibility fields are all required because
 * a guessed default here is how somebody gets told they qualify for something
 * they do not.
 */
const SCRIPT: Step[] = [
  {
    id: "full_name",
    question: "What should I call you?",
    skippable: true,
    parse: text(200, "Your name"),
  },
  {
    id: "institution",
    question: "Where do you study?",
    hint: "The full name, as a funder would write it.",
    parse: text(300, "Institution"),
  },
  {
    id: "major",
    question: "What are you studying?",
    skippable: true,
    parse: text(200, "Major"),
  },
  {
    id: "degree_level",
    question: "What level?",
    choices: [...DEGREE_CHOICES],
    parse: oneOf(DEGREE_CHOICES),
  },
  {
    id: "citizenship",
    question: "What is your citizenship or visa status?",
    hint: "As the funders' rules state it. Most programs care a great deal about this one.",
    choices: ["us_citizen", "permanent_resident", "f1_visa", "other"],
    parse: text(100, "Citizenship"),
  },
  {
    id: "building",
    question: "What are you building?",
    hint: "A few sentences. This is the only answer Kairos may quote from when it drafts, so write it the way you would say it out loud.",
    parse: text(4_000, "That answer"),
  },
  {
    id: "stage",
    question: "How far along is it?",
    choices: [...STAGE_CHOICES],
    parse: oneOf(STAGE_CHOICES),
  },
  {
    id: "entity_type",
    question: "Have you formed a legal entity?",
    hint: "Plenty of programs are open to teams that have not. Answer none if that is you.",
    choices: [...ENTITY_CHOICES],
    parse: oneOf(ENTITY_CHOICES),
  },
  {
    id: "team_size",
    question: "How many people are on the team, including you?",
    parse: wholeNumber(1, "Team size"),
  },
  {
    id: "traction.users",
    question: "How many users do you have?",
    hint: "A number. Zero is a real answer and a useful one. Skip if it does not apply.",
    skippable: true,
    parse: wholeNumber(0, "That"),
  },
  {
    id: "traction.pitches",
    question: "How many times have you pitched this?",
    hint: "Competitions, demo days, investor meetings.",
    skippable: true,
    parse: wholeNumber(0, "That"),
  },
  {
    id: "traction.revenue_usd",
    question: "Any revenue yet, in dollars?",
    skippable: true,
    parse: wholeNumber(0, "That"),
  },
  {
    id: "funding_floor",
    question: "What is the smallest award worth your time, in dollars?",
    hint: "Below this, Kairos will not surface it.",
    parse: wholeNumber(0, "The floor"),
  },
  {
    id: "funding_ceiling",
    question: "And the largest you would realistically go after?",
    parse: wholeNumber(0, "The ceiling"),
  },
  {
    id: "equity_ok",
    question: "Would you take money that costs you equity?",
    choices: ["yes", "no"],
    parse: yesNo,
  },
  {
    id: "has_faculty_advisor",
    question: "Do you have a faculty advisor?",
    hint: "Some campus programs require one. Kairos will tell you which.",
    choices: ["yes", "no"],
    parse: yesNo,
  },
  {
    id: "max_application_hours",
    question: "How many hours will you spend on one application?",
    hint: "Kairos uses this to drop the ones that would cost more than they are worth to you.",
    parse: wholeNumber(1, "Hours"),
  },
  {
    id: "geographies",
    question: "Where can you claim residency or study?",
    hint: "Comma-separated tokens, e.g. US-NJ, US.",
    skippable: true,
    parse: (raw: string): ParseResult => {
      const list = raw
        .split(",")
        .map((token) => token.trim())
        .filter(Boolean);
      if (list.length === 0) return { ok: false, message: "Give at least one, or skip." };
      return { ok: true, value: list, echo: list.join(", ") };
    },
  },
];

// ── Assembling a profile out of the answers ──────────────────────────────────

type Answers = Record<string, unknown>;

/**
 * Build the profile to send.
 *
 * Merged over `existing` when there is one, so re-running intake corrects a
 * profile rather than resetting the fields it never asked about. Prose becomes
 * a knowledge chunk here and nowhere else.
 */
function toProfile(
  answers: Answers,
  existing: FounderProfile | null,
  founderId: string,
): FounderProfile {
  const traction: Record<string, number> = { ...(existing?.traction ?? {}) };
  for (const key of ["users", "pitches", "revenue_usd"]) {
    const value = answers[`traction.${key}`];
    if (typeof value === "number") traction[key] = value;
  }

  const knowledge: KnowledgeChunk[] = [...(existing?.knowledge_base ?? [])];
  const building = answers.building;
  if (typeof building === "string" && building) {
    knowledge.push({
      chunk_id: `onboarding_${Date.now().toString(36)}`,
      text: building,
      // Named so a later reader can tell a founder's own words from a
      // scraped page. Provenance is the whole point of a chunk.
      source: "onboarding_chat",
      confidence: 1,
      created_at: new Date().toISOString(),
    });
  }

  const pick = <T,>(key: string, fallback: T): T =>
    (answers[key] as T | undefined) ?? fallback;

  return {
    founder_id: founderId,
    full_name: pick<string | null>("full_name", existing?.full_name ?? null),
    degree_level: pick<DegreeLevel>("degree_level", existing?.degree_level ?? "undergrad"),
    institution: pick("institution", existing?.institution ?? ""),
    major: pick<string | null>("major", existing?.major ?? null),
    citizenship: pick("citizenship", existing?.citizenship ?? ""),
    entity_type: pick<EntityType>("entity_type", existing?.entity_type ?? "none"),
    team_size: pick("team_size", existing?.team_size ?? 1),
    stage: pick<Stage>("stage", existing?.stage ?? "idea"),
    traction,
    funding_range: [
      pick("funding_floor", existing?.funding_range[0] ?? 0),
      pick("funding_ceiling", existing?.funding_range[1] ?? 0),
    ],
    equity_ok: pick("equity_ok", existing?.equity_ok ?? false),
    has_faculty_advisor: pick(
      "has_faculty_advisor",
      existing?.has_faculty_advisor ?? false,
    ),
    max_application_hours: pick(
      "max_application_hours",
      existing?.max_application_hours ?? 8,
    ),
    geographies: pick("geographies", existing?.geographies ?? []),
    knowledge_base: knowledge,
  };
}

// ── The conversation ─────────────────────────────────────────────────────────

interface Turn {
  who: "kairos" | "you";
  text: string;
}

type Status =
  | { phase: "asking" }
  | { phase: "saving" }
  | { phase: "saved" }
  | { phase: "error"; message: string };

/** One captured answer, shown in the live panel beside the conversation. */
function Captured({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-rule/60 py-1.5 last:border-b-0">
      <span className="text-xs uppercase tracking-[0.1em] text-ink-muted">{label}</span>
      <span className="text-right text-sm text-ink">{value}</span>
    </div>
  );
}

/**
 * The intake conversation, plus a live view of what it has recorded.
 *
 * The panel on the right is not decoration. A founder is answering questions
 * that decide which funding they are shown, so they get to watch the record
 * being written and see it in the same words the filter will use. A chat that
 * hides what it extracted is a chat you cannot correct.
 */
export function IntakeChat({
  profile,
  founderId,
}: {
  profile: FounderProfile | null;
  /**
   * Passed in rather than read from `@/lib/config`. That module is
   * `server-only` because it also holds the backend address, which the
   * browser must never learn. The founder id is not in that category — it is
   * already on screen on the profile page — so it crosses as a prop and the
   * address stays behind the proxy.
   */
  founderId: string;
}) {
  const router = useRouter();
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Answers>({});
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<Status>({ phase: "asking" });
  const [turns, setTurns] = useState<Turn[]>([
    {
      who: "kairos",
      text:
        profile === null
          ? "Before I can look for anything, I need to know a few things about you. Nothing here is a guess: every answer is compared literally against what each funder says they require."
          : "Answering again replaces what I have on file. Anything you skip stays as it is.",
    },
    { who: "kairos", text: SCRIPT[0].question },
  ]);
  const inFlight = useRef(false);
  const endRef = useRef<HTMLDivElement>(null);

  const step: Step | undefined = SCRIPT[index];
  const done = index >= SCRIPT.length;
  const saving = status.phase === "saving";

  useEffect(() => {
    // Optional-called: jsdom has no layout, so `scrollIntoView` is undefined
    // there, and a missing scroll is not worth throwing over anywhere else.
    endRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [turns]);

  const captured = useMemo(() => {
    const rows: { label: string; value: string }[] = [];
    for (const entry of SCRIPT) {
      const value = answers[entry.id];
      if (value === undefined) continue;
      rows.push({
        label: entry.id.replace("traction.", "").replace(/_/g, " "),
        value: Array.isArray(value) ? value.join(", ") : String(value),
      });
    }
    return rows;
  }, [answers]);

  /** Record one answer and move on. Advancing is the only way `index` grows. */
  function accept(id: string, value: unknown, echo: string) {
    const nextIndex = index + 1;
    const next = SCRIPT[nextIndex];
    setAnswers((prev) => ({ ...prev, [id]: value }));
    setTurns((prev) => [
      ...prev,
      { who: "you", text: echo },
      ...(next ? [{ who: "kairos" as const, text: next.question }] : []),
    ]);
    setIndex(nextIndex);
    setDraft("");
  }

  /** Parse what was typed. A rejection is a turn in the conversation, not an alert. */
  function submit(raw: string) {
    if (!step || saving) return;
    const trimmed = raw.trim();
    if (!trimmed) return;

    if (step.skippable && trimmed.toLowerCase() === "skip") {
      accept(step.id, undefined, "skip");
      return;
    }

    const result = step.parse(trimmed);
    if (!result.ok) {
      setTurns((prev) => [
        ...prev,
        { who: "you", text: trimmed },
        { who: "kairos", text: result.message },
      ]);
      setDraft("");
      return;
    }
    accept(step.id, result.value, result.echo);
  }

  /**
   * Send the assembled profile.
   *
   * One whole-object `PUT`, the same contract the profile editor uses: these
   * are the fields the filter compares against and a half-applied set of them
   * is the one outcome worth ruling out. On success the page refreshes so the
   * stored profile is what renders, not the request.
   */
  async function save() {
    if (inFlight.current) return;
    inFlight.current = true;
    setStatus({ phase: "saving" });
    try {
      const response = await fetch("/api/profile", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(toProfile(answers, profile, founderId)),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.error ?? "The profile could not be saved.");
      }
      setStatus({ phase: "saved" });
      router.refresh();
    } catch (error) {
      setStatus({
        phase: "error",
        message: error instanceof Error ? error.message : "The profile could not be saved.",
      });
    } finally {
      inFlight.current = false;
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
      <div className="rounded-lg border border-rule bg-surface p-5 sm:p-6">
        <div className="max-h-96 space-y-3 overflow-y-auto pr-1">
          {turns.map((turn, position) => (
            <p
              key={position}
              className={
                turn.who === "kairos"
                  ? "text-sm leading-relaxed text-ink"
                  : "text-sm leading-relaxed text-accent"
              }
            >
              <span className="mr-2 text-xs uppercase tracking-[0.1em] text-ink-muted">
                {turn.who === "kairos" ? "Kairos" : "You"}
              </span>
              {turn.text}
            </p>
          ))}
          <div ref={endRef} />
        </div>

        {step ? (
          <div className="mt-4 border-t border-rule pt-4">
            {step.hint ? (
              <p className="mb-2 text-xs leading-relaxed text-ink-muted">{step.hint}</p>
            ) : null}

            {step.choices ? (
              <div className="mb-2 flex flex-wrap gap-2">
                {step.choices.map((choice) => (
                  <button
                    key={choice}
                    type="button"
                    onClick={() => submit(choice)}
                    className="rounded-full border border-rule px-3 py-1 text-xs text-ink-soft hover:border-accent hover:text-ink"
                  >
                    {choice.replace(/_/g, " ")}
                  </button>
                ))}
              </div>
            ) : null}

            <form
              onSubmit={(event) => {
                event.preventDefault();
                submit(draft);
              }}
              className="flex gap-2"
            >
              <input
                type="text"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={step.skippable ? "Type an answer, or skip" : "Type an answer"}
                aria-label={step.question}
                className="w-full rounded-md border border-rule bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
              <button
                type="submit"
                className="rounded-md border border-accent px-4 py-2 text-sm text-accent hover:bg-accent hover:text-surface"
              >
                Send
              </button>
            </form>
          </div>
        ) : (
          <div className="mt-4 border-t border-rule pt-4">
            {status.phase === "saved" ? (
              <p className="text-sm text-ok">
                Saved. Kairos has what it needs; start a run below.
              </p>
            ) : (
              <>
                <p className="mb-3 text-sm leading-relaxed text-ink-soft">
                  That is everything. Check the record on the right, then save it.
                </p>
                <button
                  type="button"
                  onClick={save}
                  disabled={saving}
                  className="rounded-md border border-accent px-4 py-2 text-sm text-accent hover:bg-accent hover:text-surface disabled:opacity-50"
                >
                  {saving ? "Saving..." : "Save my profile"}
                </button>
                {status.phase === "error" ? (
                  <p className="mt-2 text-sm text-alert">{status.message}</p>
                ) : null}
              </>
            )}
          </div>
        )}
      </div>

      <div className="rounded-lg border border-rule bg-surface p-5 sm:p-6">
        <h3 className="font-serif text-base tracking-tight text-ink">
          What Kairos has recorded
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-muted">
          Every one of these is compared literally against a funder&rsquo;s stated
          rules. What you wrote about the work goes to the knowledge base instead,
          where a draft may quote it.
        </p>
        <div className="mt-3">
          {captured.length === 0 ? (
            <p className="text-sm text-ink-muted">Nothing yet.</p>
          ) : (
            captured.map((row) => (
              <Captured key={row.label} label={row.label} value={row.value} />
            ))
          )}
        </div>
        <p className="mt-4 text-xs text-ink-muted">
          {done
            ? `${captured.length} answers. Nothing is stored until you save.`
            : `Question ${Math.min(index + 1, SCRIPT.length)} of ${SCRIPT.length}.`}
        </p>
      </div>
    </div>
  );
}

/**
 * The intake section as the briefing renders it.
 *
 * A founder with no profile gets the conversation immediately and expanded,
 * because nothing else on the page can do anything for them yet. A founder who
 * already has one gets a single line and a button: their profile is not a
 * thing they need to re-answer every time they open the dashboard, but it is a
 * thing they should always be one click from correcting.
 */
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
      <div className="rounded-lg border border-rule bg-surface p-5 sm:p-6">
        <p className="text-sm leading-relaxed text-ink-soft">
          {profile
            ? `Kairos is matching against ${profile.institution}, ${profile.stage} stage, team of ${profile.team_size}.`
            : "Kairos has nothing to match against yet."}
          {profile && Object.keys(profile.traction).length === 0
            ? " It has no traction numbers for you, which is the first thing a reviewer asks about."
            : ""}
          {profile && profile.knowledge_base.length === 0
            ? " It also has nothing on record about what you are building, so a draft would have nothing to quote."
            : ""}
        </p>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="mt-3 rounded-md border border-rule px-4 py-2 text-sm text-ink-soft hover:border-accent hover:text-ink"
        >
          Tell Kairos more
        </button>
      </div>
    );
  }

  return <IntakeChat profile={profile} founderId={founderId} />;
}
