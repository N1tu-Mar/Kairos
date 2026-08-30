import type {
  Draft,
  DraftCounts,
  FounderProfile,
  InboxItem,
  Opportunity,
  RunJob,
  RunReport,
  ScraperCandidate,
  ScraperCandidateGroup,
} from "@/lib/types";

/** Shapes copied from what the FastAPI test suite actually stores. */

export function runJob(overrides: Partial<RunJob> = {}): RunJob {
  return {
    job_id: "job_abc123",
    founder_id: "founder_demo",
    idempotency_key: "manual-1",
    source: "manual",
    use_demo_catalog: false,
    include_grants_gov: true,
    status: "queued",
    created_at: "2026-08-23T06:00:00Z",
    started_at: null,
    finished_at: null,
    run_id: null,
    error: null,
    ...overrides,
  };
}

export function runReport(overrides: Partial<RunReport> = {}): RunReport {
  return {
    run_id: "run_1",
    founder_id: "founder_demo",
    started_at: "2026-08-23T06:00:00Z",
    finished_at: "2026-08-23T06:01:12Z",
    duration_s: 72.4,
    scanned: 214,
    filtered_out: 198,
    judged: 16,
    surfaced: 3,
    sources_failed: [],
    rejections: [],
    skips: [],
    usage: {
      input_tokens: 41_000,
      output_tokens: 6_200,
      total_tokens: 47_200,
      usd_estimate: 0,
    },
    halted_reason: null,
    stateless: false,
    notes: [],
    ...overrides,
  };
}

export function inboxItem(overrides: Partial<InboxItem> = {}): InboxItem {
  return {
    item_id: "run_1:opp_1",
    founder_id: "founder_demo",
    opportunity_id: "opp_1",
    kind: "APPLY",
    headline:
      "[DEMO] Campus Innovation Fund · up to $10,000 · 24 days left · ~5h of work",
    summary: "Your pilot clears this fund's stated requirement.",
    assessment: {
      verdict: "APPLY",
      reason: "Your pilot clears this fund's stated requirement.",
      effort_hours: 5,
      blocker: null,
      blocker_founder_resolvable: false,
      opportunity_id: "opp_1",
      model_id: "test-model",
      prompt_version: "abc123",
      created_at: "2026-08-23T06:00:30Z",
    },
    draft_id: "draft_1",
    passive: false,
    state: "new",
    created_at: "2026-08-23T06:00:30Z",
    ...overrides,
  };
}

export function draft(overrides: Partial<Draft> = {}): Draft {
  return {
    draft_id: "draft_1",
    founder_id: "founder_demo",
    opportunity_id: "opp_1",
    form_name: "[DEMO] Campus Innovation Fund — Application",
    status: "READY",
    created_at: "2026-08-23T06:01:00Z",
    gate_result: {
      passed: true,
      checks_run: ["BLOCKLIST", "PROVENANCE", "COMPLETENESS"],
      violations: [],
      failed_check: null,
    },
    fields: [
      {
        field_id: "problem",
        question: "What problem are you solving?",
        answer: "Students book shared lab equipment on a paper sign-up sheet.",
        status: "GENERATED",
        provenance: [
          {
            chunk_id: "deck_p1",
            source: "pitch_deck.pdf p.1",
            text: "Students currently book microscopes on a paper sign-up sheet.",
            char_start: null,
            char_end: null,
          },
        ],
        model_id: "test-model",
        prompt_version: "abc123",
        audit_verdict: "SUPPORTED",
        audit_note: "",
        reused_from: null,
        created_at: "2026-08-23T06:01:00Z",
      },
      {
        field_id: "budget",
        question: "How will you spend the award?",
        answer: null,
        status: "NEEDS_FOUNDER",
        provenance: [],
        model_id: "",
        prompt_version: "",
        audit_verdict: null,
        audit_note: "",
        reused_from: null,
        created_at: "2026-08-23T06:01:00Z",
      },
    ],
    ...overrides,
  };
}

export function counts(overrides: Partial<DraftCounts> = {}): DraftCounts {
  return {
    KNOWN: 3,
    REUSED: 2,
    GENERATED: 1,
    NEEDS_FOUNDER: 1,
    ...overrides,
  };
}

export function opportunity(overrides: Partial<Opportunity> = {}): Opportunity {
  return {
    id: "opp_1",
    title: "[DEMO] Campus Innovation Fund",
    funder: "[DEMO] Example University Office of Innovation",
    source: "seed",
    source_url: "https://example.invalid/campus-innovation-fund",
    award_min: 2_500,
    award_max: 10_000,
    deadline: "2999-10-15",
    rolling: false,
    effort_hours_estimate: 5,
    eligibility: {
      degree_levels: ["undergrad", "masters"],
      citizenships: ["us_citizen"],
      entity_types: ["none", "llc"],
      min_team_size: null,
      max_team_size: 5,
      geographies: null,
      institutions: null,
      requires_faculty_pi: null,
      takes_equity: false,
    },
    criteria: [],
    description_excerpt: "[DEMO] Seed funding for enrolled students.",
    verified: true,
    verified_at: "2026-08-22T00:00:00Z",
    retrieved_at: "2026-08-22T00:00:00Z",
    ...overrides,
  };
}

export function scraperCandidate(
  overrides: Partial<ScraperCandidate> = {},
): ScraperCandidate {
  return {
    scrape_id: "university_web_abc123",
    title: "Campus Venture Prize",
    organization: "Example University Innovation Center",
    source_url: "https://innovation.example.edu/prize",
    award_type: "cash prize",
    award_min: 1_000,
    award_max: 5_000,
    institution: ["Example University"],
    degree_levels: ["undergraduate", "graduate"],
    applicant_type: ["student founder"],
    equity_required: false,
    team_size_min: 1,
    team_size_max: 4,
    deadline: "May 1, 2027",
    deadline_iso: "2027-05-01",
    evidence: {},
    unknown_fields: ["equity_required"],
    caveats: ["[university web search] This page has not been human reviewed."],
    founder_reviews: [],
    fetch: {
      url: "https://innovation.example.edu/prize",
      final_url: "https://innovation.example.edu/prize",
      status_code: 200,
      robots_allowed: true,
      robots_url: "https://innovation.example.edu/robots.txt",
      crawl_delay_s: null,
      fetched_at: "2026-08-28T06:00:00Z",
      content_hash: "abc123",
      raw_path: "",
      renderer: "httpx",
      content_format: "html",
      fallback_reason: "",
      source_raw_path: "",
      failure: null,
      bytes: 2048,
    },
    scraped_at: "2026-08-28T06:00:00Z",
    review_status: "NEEDS_HUMAN_REVIEW",
    ...overrides,
  };
}

export function scraperCandidateGroup(
  overrides: Partial<ScraperCandidateGroup> = {},
): ScraperCandidateGroup {
  return {
    lane: "university",
    label: "university funding",
    source_file: "opportunities.university-web.candidates.json",
    total: 1,
    candidates: [scraperCandidate()],
    ...overrides,
  };
}

export function founderProfile(
  overrides: Partial<FounderProfile> = {},
): FounderProfile {
  return {
    founder_id: "founder_demo",
    full_name: "Ada Lovelace",
    degree_level: "masters",
    institution: "Rutgers University",
    major: "Computer Science",
    citizenship: "us_citizen",
    entity_type: "none",
    team_size: 2,
    stage: "prototype",
    traction: { users: 120 },
    funding_range: [5_000, 50_000],
    equity_ok: false,
    has_faculty_advisor: false,
    max_application_hours: 8,
    geographies: ["US-NJ", "US"],
    knowledge_base: [
      {
        chunk_id: "chunk_1",
        text: "[DEMO] We shipped a prototype to 120 users.",
        source: "onboarding",
        confidence: 1,
        created_at: "2026-08-22T00:00:00Z",
      },
    ],
    ...overrides,
  };
}
