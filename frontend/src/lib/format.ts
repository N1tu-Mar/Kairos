/**
 * Pure presentation helpers. No data is invented here — every function either
 * formats a value the API sent or splits a string the backend already composed.
 */

import type { RunReport } from "@/lib/types";

/** Synthetic rows are marked `[DEMO]` at the source and must stay marked. */
export function isDemo(text: string | null | undefined): boolean {
  return (text ?? "").includes("[DEMO]");
}

/**
 * Thousands-separated integer, en-US.
 */
export function formatInt(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

/**
 * Human duration from seconds. `—` for negatives and non-finite values.
 *
 * Three bands: milliseconds under a second, one decimal of seconds under a
 * minute, then `Xm Ys`. The em dash is the shared "no value" marker used
 * by every formatter here, so an unset field and a malformed one look the
 * same on screen — deliberate, since neither is something the reader can
 * act on.
 */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

/**
 * Dollars to four decimal places.
 *
 * Four, not two: a run's spend is routinely under a cent, and rounding it
 * to $0.00 would make a real cost indistinguishable from the unpriced case.
 */
export function formatUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

/**
 * Absolute date and time, in the *viewer's* locale timezone.
 *
 * The API sends UTC. This renders local, so a timestamp shown here and one
 * in a server log will not match unless the reader is on UTC. `—` for null
 * and unparseable input.
 */
export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

/**
 * Relative time ("3 hours ago"), bucketed to minutes, hours or days.
 *
 * Computed against `Date.now()` at render time. In a server-rendered page
 * that is the server's clock at render, so a page held open does not
 * update — it says how old the data was when the page was built.
 */
export function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const deltaMinutes = Math.round((then - Date.now()) / 60_000);
  const formatter = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });
  const abs = Math.abs(deltaMinutes);
  if (abs < 60) return formatter.format(deltaMinutes, "minute");
  if (abs < 60 * 24) return formatter.format(Math.round(deltaMinutes / 60), "hour");
  return formatter.format(Math.round(deltaMinutes / (60 * 24)), "day");
}

/** Date-only ISO string ("2026-10-15") to "Oct 15, 2026". */
export function formatDate(isoDate: string | null): string {
  if (!isoDate) return "—";
  const date = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium" }).format(date);
}

/** Whole days from today until a date-only ISO string. Negative = past. */
export function daysUntil(isoDate: string | null): number | null {
  if (!isoDate) return null;
  const then = new Date(`${isoDate}T00:00:00`).getTime();
  if (Number.isNaN(then)) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((then - today.getTime()) / 86_400_000);
}

/**
 * "$2,500 – $10,000", "up to $10,000", "$2,500+", or null when the source
 * stated no award at all. Null stays null — this app does not invent a range.
 */
export function formatAwardRange(
  min: number | null,
  max: number | null,
): string | null {
  if (min != null && max != null) {
    return min === max
      ? `$${formatInt(max)}`
      : `$${formatInt(min)} – $${formatInt(max)}`;
  }
  if (max != null) return `up to $${formatInt(max)}`;
  if (min != null) return `$${formatInt(min)}+`;
  return null;
}

/**
 * The backend composes the headline in Python as
 * `title · up to $X · N days left · ~Yh of work` (agent/scout.py::_headline).
 * Splitting on the separator is typography, not parsing: the first part is
 * always the opportunity title and the remainder are already-rendered facts.
 */
export function splitHeadline(headline: string): {
  title: string;
  facts: string[];
} {
  const parts = headline.split(" · ").map((part) => part.trim()).filter(Boolean);
  if (parts.length === 0) return { title: headline, facts: [] };
  const [title, ...facts] = parts;
  return { title, facts };
}

/** The four counters, in the order the pitch says them. */
export function runCounters(report: RunReport) {
  return [
    { label: "Scanned", value: report.scanned, hint: "opportunities seen by a source" },
    { label: "Discarded", value: report.filtered_out, hint: "dropped by the deterministic filter" },
    { label: "Judged", value: report.judged, hint: "sent to the Assessor" },
    { label: "Surfaced", value: report.surfaced, hint: "shown to you" },
  ] as const;
}

/** Headline sentence, mirroring `RunReport.headline()` in Python. */
export function runHeadline(report: RunReport): string {
  return `Scanned ${report.scanned}. Discarded ${report.filtered_out}. Judged ${report.judged}. Surfaced ${report.surfaced}.`;
}

/**
 * Split on underscores and whitespace, capitalise each word.
 *
 * Used for machine tokens (`first_degree`, `not_done`). Only the first
 * letter is touched, so an acronym keeps whatever case it arrived in.
 */
export function titleCase(token: string): string {
  return token
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export const SKIP_STAGE_LABELS: Record<string, string> = {
  hard_filter: "Deterministic filter",
  assessor: "Assessor",
  escalation_policy: "Escalation policy",
};

export const FIELD_STATUS_LABELS: Record<string, string> = {
  KNOWN: "Known",
  REUSED: "Reused",
  GENERATED: "Generated",
  NEEDS_FOUNDER: "Needs you",
};
