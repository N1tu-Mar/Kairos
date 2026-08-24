/**
 * TypeScript mirrors of the Pydantic v2 models in `agent/models.py`.
 *
 * Every field here exists on the wire. Nothing is invented, and nothing the
 * API does not currently expose is declared as if it did — notably
 * `Opportunity` itself is never returned by any endpoint, so award ranges and
 * deadlines are only available as the pre-rendered text inside
 * `InboxItem.headline`, which the backend composes deterministically in
 * Python (`agent/scout.py::_headline`).
 *
 * Datetimes and dates arrive as ISO 8601 strings.
 */

// ── Vocabularies (agent/models.py) ───────────────────────────────────────────

export type DegreeLevel = "undergrad" | "masters" | "phd" | "postdoc";
export type EntityType = "none" | "llc" | "c_corp" | "s_corp" | "nonprofit";
export type Stage = "idea" | "prototype" | "mvp" | "pilot" | "revenue";
export type Verdict = "APPLY" | "MAYBE" | "SKIP" | "INSUFFICIENT_INFO";
export type FieldStatus = "KNOWN" | "GENERATED" | "NEEDS_FOUNDER" | "REUSED";
export type AuditVerdict = "SUPPORTED" | "UNSUPPORTED" | "UNVERIFIABLE";
export type DraftStatus = "DRAFT" | "READY" | "BLOCKED";
export type SourceName = "seed" | "grants_gov" | "browser";
export type SkipStage = "hard_filter" | "assessor" | "escalation_policy";
export type InboxKind =
  | "APPLY"
  | "MAYBE"
  | "UNKNOWN_HIGH_VALUE"
  | "DEADLINE_URGENT"
  | "COLD_START";
export type InboxState = "new" | "opened" | "dismissed" | "applied";

export const FIELD_STATUSES: FieldStatus[] = [
  "KNOWN",
  "REUSED",
  "GENERATED",
  "NEEDS_FOUNDER",
];

// ── Founder ──────────────────────────────────────────────────────────────────

export interface KnowledgeChunk {
  chunk_id: string;
  text: string;
  source: string;
  confidence: number;
  created_at: string;
}

export interface FounderProfile {
  founder_id: string;
  degree_level: DegreeLevel;
  institution: string;
  citizenship: string;
  entity_type: EntityType;
  team_size: number;
  stage: Stage;
  traction: Record<string, number>;
  /** Pydantic `tuple[int, int]` serialises as a two-element array. */
  funding_range: [number, number];
  equity_ok: boolean;
  has_faculty_advisor: boolean;
  max_application_hours: number;
  geographies: string[];
  knowledge_base: KnowledgeChunk[];
}

// ── Run report ───────────────────────────────────────────────────────────────

export interface SourceFailure {
  source: SourceName;
  detail: string;
  at: string;
}

export interface Rejection {
  opportunity_id: string;
  opportunity_title: string;
  /** Machine id of the deterministic check, e.g. "DEGREE_LEVEL". */
  check: string;
  detail: string;
  founder_value: string;
  required_value: string;
}

export interface SkipRecord {
  opportunity_id: string;
  opportunity_title: string;
  stage: SkipStage;
  reason: string;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  usd_estimate: number;
}

export interface RunReport {
  run_id: string;
  founder_id: string;
  started_at: string;
  finished_at: string | null;
  duration_s: number;

  scanned: number;
  filtered_out: number;
  judged: number;
  surfaced: number;

  sources_failed: SourceFailure[];
  rejections: Rejection[];
  skips: SkipRecord[];
  usage: TokenUsage;
  halted_reason: string | null;
  stateless: boolean;
  notes: string[];
}

/** Response shape of `GET /founders/{id}/runs/latest/skips`. */
export interface LatestSkips {
  run_id: string;
  headline: string;
  rejections: Rejection[];
  skips: SkipRecord[];
  sources_failed: SourceFailure[];
  notes: string[];
}

// ── Inbox ────────────────────────────────────────────────────────────────────

export interface Assessment {
  verdict: Verdict;
  reason: string;
  effort_hours: number;
  blocker: string | null;
  blocker_founder_resolvable: boolean;
  opportunity_id: string;
  model_id: string;
  prompt_version: string;
  created_at: string;
}

export interface InboxItem {
  item_id: string;
  founder_id: string;
  opportunity_id: string;
  kind: InboxKind;
  /** Composed in Python; already carries award, days-left and effort. */
  headline: string;
  summary: string;
  assessment: Assessment | null;
  draft_id: string | null;
  /** Overflow past MAX_SURFACED_PER_RUN. Visible, never notified. */
  passive: boolean;
  state: InboxState;
  created_at: string;
}

// ── Draft ────────────────────────────────────────────────────────────────────

export interface SourceSpan {
  chunk_id: string;
  source: string;
  text: string;
  char_start: number | null;
  char_end: number | null;
}

export interface DraftField {
  field_id: string;
  question: string;
  answer: string | null;
  status: FieldStatus;
  provenance: SourceSpan[];
  model_id: string;
  prompt_version: string;
  audit_verdict: AuditVerdict | null;
  audit_note: string;
  reused_from: string | null;
  created_at: string;
}

export interface GateViolation {
  check: string;
  field_id: string | null;
  detail: string;
  severity: "BLOCK" | "FORCED_NEEDS_FOUNDER";
}

export interface GateResult {
  passed: boolean;
  checks_run: string[];
  violations: GateViolation[];
  failed_check: string | null;
}

export interface Draft {
  draft_id: string;
  founder_id: string;
  opportunity_id: string;
  form_name: string;
  fields: DraftField[];
  status: DraftStatus;
  gate_result: GateResult | null;
  created_at: string;
}

export type DraftCounts = Record<FieldStatus, number>;

/** Response shape of `GET /drafts/{draft_id}`. Counts are computed in Python. */
export interface DraftResponse {
  draft: Draft;
  counts: DraftCounts;
}

// ── Manual run trigger ───────────────────────────────────────────────────────

/** Body of `POST /founders/{id}/runs`. */
export interface RunTrigger {
  use_demo_catalog: boolean;
  include_grants_gov: boolean;
}
