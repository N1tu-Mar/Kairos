/**
 * TypeScript mirrors of the Pydantic v2 models in `agent/models.py`.
 *
 * Every field here exists on the wire. Nothing is invented, and nothing the
 * API does not currently expose is declared as if it did. `Opportunity` is
 * served by `GET /opportunities/{id}` — runs persist every opportunity they
 * retrieved — so award ranges, deadlines and eligibility rules are structured
 * fields here, not text parsed back out of a headline.
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

/** Editable vocabularies, for the profile editor's selects. */
export const DEGREE_LEVELS: DegreeLevel[] = [
  "undergrad",
  "masters",
  "phd",
  "postdoc",
];
export const ENTITY_TYPES: EntityType[] = [
  "none",
  "llc",
  "c_corp",
  "s_corp",
  "nonprofit",
];
export const STAGES: Stage[] = ["idea", "prototype", "mvp", "pilot", "revenue"];
export const INBOX_STATES: InboxState[] = [
  "new",
  "opened",
  "dismissed",
  "applied",
];

// ── Opportunity (agent/models.py) ────────────────────────────────────────────

/**
 * Machine-checkable eligibility, extracted from source text. `null` means the
 * source did not state it — NOT "no restriction". The Python filter maps
 * `null` to UNKNOWN, and UNKNOWN becomes a founder-facing question.
 */
export interface EligibilityRules {
  degree_levels: DegreeLevel[] | null;
  citizenships: string[] | null;
  entity_types: EntityType[] | null;
  min_team_size: number | null;
  max_team_size: number | null;
  geographies: string[] | null;
  institutions: string[] | null;
  requires_faculty_pi: boolean | null;
  takes_equity: boolean | null;
}

/** A criterion lifted verbatim from a source document. */
export interface ExtractedCriterion {
  text: string;
  source_doc: string;
  char_start: number | null;
  char_end: number | null;
}

/** One funding opportunity, as `GET /opportunities/{id}` returns it. */
export interface Opportunity {
  id: string;
  title: string;
  funder: string;
  source: SourceName;
  source_url: string;
  award_min: number | null;
  award_max: number | null;
  /** ISO date (no time). */
  deadline: string | null;
  rolling: boolean;
  effort_hours_estimate: number | null;
  eligibility: EligibilityRules;
  criteria: ExtractedCriterion[];
  description_excerpt: string;
  /** False until a human opened `source_url` and confirmed the row. */
  verified: boolean;
  verified_at: string | null;
  retrieved_at: string;
}

// Scraper candidates

export type ScraperLaneName = "university" | "general";
export type ScraperReviewStatus = "NEEDS_HUMAN_REVIEW" | "ACCEPTED" | "REJECTED";

export interface ScraperEvidence {
  text: string;
  source_url: string;
  method: string;
}

export interface ScraperFetchRecord {
  url: string;
  final_url: string;
  status_code: number | null;
  robots_allowed: boolean;
  robots_url: string;
  crawl_delay_s: number | null;
  fetched_at: string;
  content_hash: string;
  raw_path: string;
  renderer: "httpx" | "playwright";
  failure: string | null;
  bytes: number;
}

export interface ScraperCandidate {
  scrape_id: string;
  title: string;
  organization: string;
  source_url: string;
  award_type: string | null;
  award_min: number | null;
  award_max: number | null;
  institution: string[] | null;
  degree_levels: string[] | null;
  applicant_type: string[] | null;
  equity_required: boolean | null;
  team_size_min: number | null;
  team_size_max: number | null;
  deadline: string | null;
  deadline_iso: string | null;
  evidence: Record<string, ScraperEvidence>;
  unknown_fields: string[];
  caveats: string[];
  founder_reviews: unknown[];
  fetch: ScraperFetchRecord;
  scraped_at: string;
  review_status: ScraperReviewStatus;
}

export interface ScraperCandidateGroup {
  lane: ScraperLaneName;
  label: string;
  source_file: string;
  total: number;
  candidates: ScraperCandidate[];
}

export type ScraperCandidateGroups = Partial<
  Record<ScraperLaneName, ScraperCandidateGroup>
>;

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
  /** What to call this founder. Never read by the eligibility filter. */
  full_name: string | null;
  degree_level: DegreeLevel;
  institution: string;
  /** Field of study, as the founder writes it. Context, not a criterion. */
  major: string | null;
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
  /** Makes a retry resolve to the same logical run instead of a second one. */
  idempotency_key?: string;
  source?: "manual" | "scheduled";
}

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "halted"
  | "failed"
  | "cancelled";

/**
 * One accepted invocation of the pipeline. The run itself takes minutes, so
 * `POST /founders/{id}/runs` returns this immediately and the dashboard polls
 * `GET /founders/{id}/jobs/{job_id}` until it reaches a terminal status.
 *
 * `halted` is a *finished* run that stopped for a recorded reason (a budget
 * cap, throttling). It has a report. `failed` is a run that could not report
 * at all.
 */
export interface RunJob {
  job_id: string;
  founder_id: string;
  idempotency_key: string | null;
  source: "manual" | "scheduled" | "unknown";
  use_demo_catalog: boolean;
  include_grants_gov: boolean;

  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;

  run_id: string | null;
  error: string | null;
}

export interface JobStatusResponse {
  job: RunJob;
  /** Null until the run produces one. A halted run has one too. */
  report: RunReport | null;
}

/** One invocation that failed to start or finish. Sanitised server-side. */
export interface SchedulerFailure {
  founder_id: string;
  at: string;
  source: string;
  retry_count: number;
  failure_class: string;
  detail: string;
}
