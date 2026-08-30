"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { DEGREE_LEVELS, ENTITY_TYPES, STAGES } from "@/lib/types";
import type {
  DegreeLevel,
  EntityType,
  FounderProfile,
  Stage,
} from "@/lib/types";

/**
 * Edits the structured facts the deterministic eligibility filter compares
 * against — and sends the WHOLE profile back, never a patch.
 *
 * That is the backend's contract (`PUT /founders/{id}` replaces wholesale),
 * and it exists for a reason: a half-applied update — citizenship changed,
 * degree level not — is how a founder gets told they are eligible for
 * something they are not. The traction numbers and the knowledge base are
 * passed through untouched: this form edits eligibility facts, not evidence.
 */

interface EditableFields {
  full_name: string;
  major: string;
  institution: string;
  degree_level: DegreeLevel;
  citizenship: string;
  entity_type: EntityType;
  stage: Stage;
  team_size: string;
  funding_floor: string;
  funding_ceiling: string;
  equity_ok: boolean;
  has_faculty_advisor: boolean;
  max_application_hours: string;
  geographies: string;
  reuse_eligibility_answers: boolean;
}

/**
 * Project a profile into the form's editable shape.
 *
 * Numbers become strings because an in-progress input can legitimately be
 * empty or half-typed, which a `number` state cannot hold. They are parsed
 * back in `save`, after `validate`.
 *
 * Name and field of study travel with the eligibility fields even though the
 * filter never reads them: they are the two things a founder most expects to
 * be able to correct about themselves. Traction and the knowledge base are
 * still not editable here and must survive a save untouched.
 */
function fromProfile(profile: FounderProfile): EditableFields {
  return {
    full_name: profile.full_name ?? "",
    major: profile.major ?? "",
    institution: profile.institution,
    degree_level: profile.degree_level,
    citizenship: profile.citizenship,
    entity_type: profile.entity_type,
    stage: profile.stage,
    team_size: String(profile.team_size),
    funding_floor: String(profile.funding_range[0]),
    funding_ceiling: String(profile.funding_range[1]),
    equity_ok: profile.equity_ok,
    has_faculty_advisor: profile.has_faculty_advisor,
    max_application_hours: String(profile.max_application_hours),
    geographies: profile.geographies.join(", "),
    reuse_eligibility_answers: profile.reuse_eligibility_answers,
  };
}

/** Light client checks only. Pydantic on the backend is the validator of record. */
function validate(fields: EditableFields): string | null {
  const teamSize = Number(fields.team_size);
  const floor = Number(fields.funding_floor);
  const ceiling = Number(fields.funding_ceiling);
  const hours = Number(fields.max_application_hours);
  if (!fields.institution.trim()) return "Institution cannot be empty.";
  if (!fields.citizenship.trim()) return "Citizenship cannot be empty.";
  if (!Number.isInteger(teamSize) || teamSize < 1)
    return "Team size must be a whole number of at least 1.";
  if (!Number.isFinite(floor) || floor < 0)
    return "The funding floor must be a non-negative number.";
  if (!Number.isFinite(ceiling) || ceiling < 0)
    return "The funding ceiling must be a non-negative number.";
  if (floor > ceiling)
    return "The funding floor cannot be above the ceiling.";
  if (!Number.isFinite(hours) || hours <= 0)
    return "Hours per application must be a positive number.";
  return null;
}

const INPUT_CLASS =
  "w-full rounded-md border border-rule bg-surface px-3 py-2 text-sm text-ink " +
  "focus:border-accent focus:outline-none disabled:opacity-50";

/**
 * A labelled form row. Layout only; no validation and no state.
 */
function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-[0.12em] text-ink-muted">
        {label}
      </span>
      {children}
      {hint ? <span className="mt-1 block text-xs text-ink-muted">{hint}</span> : null}
    </label>
  );
}

type Status =
  | { phase: "idle" }
  | { phase: "saving" }
  | { phase: "saved" }
  | { phase: "error"; message: string };

/**
 * Read-only profile view with an inline editor for the eligibility fields.
 *
 * These are the fields the deterministic filter compares against, which is
 * why the save is a whole-object replace and why it is validated before it
 * is sent.
 */
export function ProfileEditor({ profile }: { profile: FounderProfile }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [fields, setFields] = useState<EditableFields>(() => fromProfile(profile));
  const [status, setStatus] = useState<Status>({ phase: "idle" });
  const inFlight = useRef(false);

  const saving = status.phase === "saving";

  /**
   * Update one field. Uses the functional form, so two edits in the same tick cannot clobber each other.
   */
  function set<K extends keyof EditableFields>(key: K, value: EditableFields[K]) {
    setFields((prev) => ({ ...prev, [key]: value }));
  }

  /**
   * Enter edit mode, re-seeding the form from the current profile.
   *
   * Re-seeding on open is what discards an abandoned edit: closing without
   * saving leaves stale values in state, and this is where they are dropped.
   */
  function open() {
    setFields(fromProfile(profile));
    setStatus({ phase: "idle" });
    setEditing(true);
  }

  /**
   * Validate, PUT the whole profile, then refresh so the stored version renders.
   *
   * A whole-object replace, never a patch — a half-applied update to these
   * fields is how a founder gets told they are eligible for something they
   * are not. Edited fields are merged *over* the existing profile, so
   * traction and the knowledge base survive.
   *
   * The editor closes only on success; on failure it stays open with the
   * typed values intact so the edit is not lost.
   */
  async function save() {
    if (inFlight.current) return;
    const problem = validate(fields);
    if (problem) {
      setStatus({ phase: "error", message: problem });
      return;
    }
    inFlight.current = true;
    setStatus({ phase: "saving" });

    // The whole object: edited eligibility facts merged over everything the
    // profile already holds, traction and knowledge base untouched.
    const next: FounderProfile = {
      ...profile,
      // Empty means "not said", which is null on the wire, not "".
      full_name: fields.full_name.trim() || null,
      major: fields.major.trim() || null,
      institution: fields.institution.trim(),
      degree_level: fields.degree_level,
      citizenship: fields.citizenship.trim(),
      entity_type: fields.entity_type,
      stage: fields.stage,
      team_size: Number(fields.team_size),
      funding_range: [Number(fields.funding_floor), Number(fields.funding_ceiling)],
      equity_ok: fields.equity_ok,
      has_faculty_advisor: fields.has_faculty_advisor,
      max_application_hours: Number(fields.max_application_hours),
      geographies: fields.geographies
        .split(",")
        .map((geo) => geo.trim())
        .filter(Boolean),
      reuse_eligibility_answers: fields.reuse_eligibility_answers,
    };

    try {
      const response = await fetch("/api/profile", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(next),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as {
          error?: string;
        } | null;
        setStatus({
          phase: "error",
          message: body?.error ?? `The backend returned ${response.status}.`,
        });
        return;
      }
      setStatus({ phase: "saved" });
      setEditing(false);
      // The read-only view above renders what the backend stored. Refresh it.
      router.refresh();
    } catch (caught) {
      setStatus({
        phase: "error",
        message:
          caught instanceof Error
            ? caught.message
            : "The request did not reach the API.",
      });
    } finally {
      inFlight.current = false;
    }
  }

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={open}
          className="rounded-md border border-rule-strong bg-sunk px-4 py-2 text-sm font-medium text-ink transition-colors hover:border-accent hover:bg-accent-soft"
        >
          Edit these facts
        </button>
        {status.phase === "saved" ? (
          <p role="status" className="text-sm text-ok">
            Saved. The view above shows what the backend stored.
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
      className="rounded-lg border border-rule bg-surface p-5 sm:p-6"
    >
      <p className="mb-5 text-sm leading-relaxed text-ink-muted">
        Saving replaces the whole profile at once. The backend refuses partial
        updates, so the eligibility filter never runs on a half-applied one.
        Traction numbers and the knowledge base are not editable here and are
        sent through unchanged.
      </p>

      <fieldset disabled={saving} className="grid gap-4 sm:grid-cols-2">
        <Field label="Your name" hint="Optional. Used to address a draft.">
          <input
            type="text"
            value={fields.full_name}
            onChange={(event) => set("full_name", event.target.value)}
            className={INPUT_CLASS}
            disabled={saving}
          />
        </Field>

        <Field label="Institution">
          <input
            className={INPUT_CLASS}
            value={fields.institution}
            onChange={(e) => set("institution", e.target.value)}
          />
        </Field>
        <Field label="Degree level">
          <select
            className={INPUT_CLASS}
            value={fields.degree_level}
            onChange={(e) => set("degree_level", e.target.value as DegreeLevel)}
          >
            {DEGREE_LEVELS.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Major" hint="Optional. Context for drafting, never a filter.">
          <input
            type="text"
            value={fields.major}
            onChange={(event) => set("major", event.target.value)}
            className={INPUT_CLASS}
            disabled={saving}
          />
        </Field>

        <Field label="Citizenship" hint="As the funders' rules state it, e.g. us_citizen.">
          <input
            className={INPUT_CLASS}
            value={fields.citizenship}
            onChange={(e) => set("citizenship", e.target.value)}
          />
        </Field>
        <Field label="Entity type">
          <select
            className={INPUT_CLASS}
            value={fields.entity_type}
            onChange={(e) => set("entity_type", e.target.value as EntityType)}
          >
            {ENTITY_TYPES.map((entity) => (
              <option key={entity} value={entity}>
                {entity}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Stage">
          <select
            className={INPUT_CLASS}
            value={fields.stage}
            onChange={(e) => set("stage", e.target.value as Stage)}
          >
            {STAGES.map((stage) => (
              <option key={stage} value={stage}>
                {stage}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Team size">
          <input
            type="number"
            min={1}
            className={INPUT_CLASS}
            value={fields.team_size}
            onChange={(e) => set("team_size", e.target.value)}
          />
        </Field>
        <Field label="Funding floor ($)">
          <input
            type="number"
            min={0}
            className={INPUT_CLASS}
            value={fields.funding_floor}
            onChange={(e) => set("funding_floor", e.target.value)}
          />
        </Field>
        <Field label="Funding ceiling ($)">
          <input
            type="number"
            min={0}
            className={INPUT_CLASS}
            value={fields.funding_ceiling}
            onChange={(e) => set("funding_ceiling", e.target.value)}
          />
        </Field>
        <Field label="Hours per application">
          <input
            type="number"
            min={1}
            className={INPUT_CLASS}
            value={fields.max_application_hours}
            onChange={(e) => set("max_application_hours", e.target.value)}
          />
        </Field>
        <Field label="Geographies" hint="Comma-separated, e.g. US-NJ, US.">
          <input
            className={INPUT_CLASS}
            value={fields.geographies}
            onChange={(e) => set("geographies", e.target.value)}
          />
        </Field>
        <label className="flex items-start gap-2.5 text-sm text-ink-soft">
          <input
            type="checkbox"
            className="mt-1 accent-[var(--accent)]"
            checked={fields.equity_ok}
            onChange={(e) => set("equity_ok", e.target.checked)}
          />
          <span>
            Open to equity
            <span className="block text-xs text-ink-muted">
              Unchecked means non-dilutive funding only.
            </span>
          </span>
        </label>
        <label className="flex items-start gap-2.5 text-sm text-ink-soft">
          <input
            type="checkbox"
            className="mt-1 accent-[var(--accent)]"
            checked={fields.has_faculty_advisor}
            onChange={(e) => set("has_faculty_advisor", e.target.checked)}
          />
          <span>
            Faculty advisor
            <span className="block text-xs text-ink-muted">
              Some funders require a faculty PI.
            </span>
          </span>
        </label>
        <label className="flex items-start gap-2.5 text-sm text-ink-soft sm:col-span-2">
          <input
            type="checkbox"
            className="mt-1 accent-[var(--accent)]"
            checked={fields.reuse_eligibility_answers}
            onChange={(e) => set("reuse_eligibility_answers", e.target.checked)}
          />
          <span>
            Reuse answers across similar eligibility requirements
            <span className="block text-xs text-ink-muted">
              Off keeps reuse to exact requirement matches.
            </span>
          </span>
        </label>
      </fieldset>

      {status.phase === "error" ? (
        <p role="alert" className="mt-4 text-sm text-alert">
          {status.message}
        </p>
      ) : null}

      <div className="mt-5 flex items-center gap-3">
        <button
          type="submit"
          disabled={saving}
          aria-busy={saving}
          className="rounded-md bg-ink px-4 py-2 text-sm font-medium text-paper transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save the whole profile"}
        </button>
        <button
          type="button"
          onClick={() => setEditing(false)}
          disabled={saving}
          className="text-sm text-ink-muted underline underline-offset-4 hover:text-ink disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
