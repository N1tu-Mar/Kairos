/**
 * Pure presentation helpers. No data is invented here — every function either
 * formats a value the API sent or splits a string the backend already composed.
 */

import type { RunReport } from "@/lib/types";

/** Synthetic rows are marked `[DEMO]` at the source and must stay marked. */
export function isDemo(text: string | null | undefined): boolean {
  return (text ?? "").includes("[DEMO]");
}

export function formatInt(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

export function formatUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

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
