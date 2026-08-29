import "server-only";

import { apiBaseUrl, apiToken, founderId, readTimeoutMs } from "@/lib/config";
import type {
  DraftResponse,
  FounderProfile,
  InboxItem,
  InboxState,
  JobStatusResponse,
  Opportunity,
  RunJob,
  RunReport,
  RunTrigger,
  SchedulerFailure,
  ScraperCandidateGroups,
} from "@/lib/types";

/**
 * The single place this app talks to FastAPI.
 *
 * FastAPI stays the source of truth. Nothing here re-implements eligibility,
 * assessment, drafting, gating or persistence — it reads what the Python
 * pipeline already decided and wrote. Three writes exist, each a thin call to
 * an endpoint the backend deliberately shaped: the manual run trigger, the
 * inbox-state patch (state and nothing else), and the whole-object profile
 * replace. Nothing here can edit a recorded verdict — no such endpoint exists.
 */

export type ApiErrorKind =
  | "not_found"
  | "timeout"
  | "unreachable"
  | "http"
  | "malformed";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly path: string;

  constructor(
    kind: ApiErrorKind,
    message: string,
    path: string,
    status?: number,
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.path = path;
    this.status = status;
  }

  /** One line the UI can show a human without leaking a stack trace. */
  get userMessage(): string {
    switch (this.kind) {
      case "unreachable":
        return `Could not reach the Kairos API at ${apiBaseUrl()}. Is the FastAPI backend running?`;
      case "timeout":
        return "The Kairos API did not respond in time.";
      case "not_found":
        return "The Kairos API has no record of that yet.";
      case "malformed":
        return "The Kairos API returned a response this dashboard could not read.";
      default:
        return `The Kairos API returned ${this.status ?? "an error"}.`;
    }
  }
}

/** Maps an ApiError onto the status a route handler should forward. */
export function httpStatusFor(error: ApiError): number {
  switch (error.kind) {
    case "not_found":
      return 404;
    case "timeout":
      return 504;
    case "unreachable":
      return 502;
    default:
      return error.status ?? 502;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT";
  body?: unknown;
  timeoutMs?: number;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, timeoutMs = readTimeoutMs() } = options;
  const url = `${apiBaseUrl()}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    const headers: Record<string, string> = {};
    if (body) headers["content-type"] = "application/json";
    // Attached server-side only; the browser never sees this credential.
    const token = apiToken();
    if (token) headers.authorization = `Bearer ${token}`;
    response = await fetch(url, {
      method,
      headers: Object.keys(headers).length > 0 ? headers : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      // Every view reflects live pipeline state; a stale render is a lie.
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new ApiError("timeout", `Timed out after ${timeoutMs}ms`, path);
    }
    throw new ApiError(
      "unreachable",
      error instanceof Error ? error.message : "fetch failed",
      path,
    );
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 404) {
    throw new ApiError("not_found", `404 for ${path}`, path, 404);
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(
      "http",
      `${response.status} for ${path}${detail ? `: ${detail.slice(0, 400)}` : ""}`,
      path,
      response.status,
    );
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("malformed", `Non-JSON response from ${path}`, path);
  }
}

/** `null` when the resource genuinely does not exist yet, rather than throwing. */
async function optional<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch (error) {
    if (error instanceof ApiError && error.kind === "not_found") return null;
    throw error;
  }
}

// ── Reads ────────────────────────────────────────────────────────────────────

export function getHealth(): Promise<{ status: string }> {
  return request("/health");
}

export function getProfile(id = founderId()): Promise<FounderProfile> {
  return request(`/founders/${encodeURIComponent(id)}`);
}

export function getProfileOrNull(id = founderId()): Promise<FounderProfile | null> {
  return optional(getProfile(id));
}

export function getInbox(
  id = founderId(),
  includePassive = true,
): Promise<InboxItem[]> {
  const query = includePassive ? "" : "?include_passive=false";
  return request(`/founders/${encodeURIComponent(id)}/inbox${query}`);
}

export function listRuns(id = founderId(), limit = 20): Promise<RunReport[]> {
  return request(`/founders/${encodeURIComponent(id)}/runs?limit=${limit}`);
}

/**
 * `null` means "no run has ever been recorded", which is a first-boot state,
 * not an error. A run that scanned and surfaced nothing is a *successful*
 * run and comes back as a normal RunReport.
 */
export function getLatestRun(id = founderId()): Promise<RunReport | null> {
  return optional(request<RunReport>(`/founders/${encodeURIComponent(id)}/runs/latest`));
}

/**
 * A single historical run, however old. `list_runs` is capped, so this reads
 * `GET /founders/{id}/runs/{run_id}` — scoped to the founder so a mistyped id
 * 404s instead of quietly resolving to someone else's run.
 */
export function getRun(
  runId: string,
  id = founderId(),
): Promise<RunReport | null> {
  return optional(
    request<RunReport>(
      `/founders/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}`,
    ),
  );
}

/**
 * The structured row a verdict was made about: award range, deadline,
 * eligibility rules and the funder's URL as fields, not as text buried in a
 * composed headline.
 */
export function getOpportunity(opportunityId: string): Promise<Opportunity> {
  return request(`/opportunities/${encodeURIComponent(opportunityId)}`);
}

export function getScraperCandidates(limit = 4): Promise<ScraperCandidateGroups> {
  return request(`/scraper/candidates?limit=${limit}`);
}

/**
 * Opportunities for a set of ids, keyed by id. An id that fails to resolve —
 * missing row, backend hiccup — is simply absent, so a view can fall back to
 * the headline the run composed rather than failing the whole page.
 */
export async function getOpportunities(
  ids: string[],
): Promise<Map<string, Opportunity>> {
  const unique = [...new Set(ids)];
  const settled = await Promise.allSettled(unique.map((id) => getOpportunity(id)));
  const map = new Map<string, Opportunity>();
  settled.forEach((result, index) => {
    if (result.status === "fulfilled") map.set(unique[index], result.value);
  });
  return map;
}

/**
 * Every draft for a founder, newest first — including one whose inbox item
 * was never created or has since been dismissed. Counts come from
 * `Draft.counts()` in Python.
 */
export function listDrafts(
  id = founderId(),
  opportunityId?: string,
): Promise<DraftResponse[]> {
  const query = opportunityId
    ? `?opportunity_id=${encodeURIComponent(opportunityId)}`
    : "";
  return request(`/founders/${encodeURIComponent(id)}/drafts${query}`);
}

export function getDraft(draftId: string): Promise<DraftResponse> {
  return request(`/drafts/${encodeURIComponent(draftId)}`);
}

export function getDraftOrNull(draftId: string): Promise<DraftResponse | null> {
  return optional(getDraft(draftId));
}

// ── Writes ───────────────────────────────────────────────────────────────────

/**
 * Accepts a run and returns the job immediately — it does *not* wait for the
 * run to finish. A run takes minutes; the backend answers 202 with a job id
 * and the caller polls {@link getJobStatus}.
 *
 * `idempotency_key` makes a retry resolve to the same logical invocation
 * (the backend answers 200 with the original job). A 409 means another run
 * already holds the lease for this founder.
 *
 * This is still a manual trigger, not a schedule — production scheduling is
 * EventBridge calling this same endpoint with `source: "scheduled"`.
 */
export function triggerRun(
  trigger: RunTrigger,
  id = founderId(),
): Promise<RunJob> {
  return request(`/founders/${encodeURIComponent(id)}/runs`, {
    method: "POST",
    body: trigger,
    // A short timeout now: this call only creates a job. The run's own
    // ceiling lives in the backend (KAIROS_RUN_TIMEOUT_S), not in a socket.
    timeoutMs: readTimeoutMs(),
  });
}

/** One job plus its report once the run has produced one. The poll target. */
export function getJobStatus(
  jobId: string,
  id = founderId(),
): Promise<JobStatusResponse> {
  return request(
    `/founders/${encodeURIComponent(id)}/jobs/${encodeURIComponent(jobId)}`,
  );
}

export function listJobs(id = founderId(), limit = 20): Promise<RunJob[]> {
  return request(`/founders/${encodeURIComponent(id)}/jobs?limit=${limit}`);
}

/**
 * Asks the backend to stop a running job. Cooperative — the run stops at its
 * next await point. What it already persisted stays persisted.
 */
export function cancelJob(
  jobId: string,
  id = founderId(),
): Promise<{ cancelled: boolean; status: string }> {
  return request(
    `/founders/${encodeURIComponent(id)}/jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST" },
  );
}

/**
 * Invocations that failed to start or finish, newest first. Sanitised
 * server-side — no credentials, no prompts, no stack traces. This is how a
 * founder learns that last night's scheduled run never ran.
 */
export function listSchedulerFailures(
  id = founderId(),
  limit = 5,
): Promise<SchedulerFailure[]> {
  return request(
    `/founders/${encodeURIComponent(id)}/scheduler/failures?limit=${limit}`,
  );
}

/**
 * Records what the founder did with an inbox item: opened, dismissed,
 * applied. `state` is the only field the backend lets anyone change — the
 * kind, headline, summary and assessment are what the run decided and an
 * audit trail you can edit is not one.
 */
export function setInboxState(
  itemId: string,
  state: InboxState,
): Promise<InboxItem> {
  return request(`/inbox/${encodeURIComponent(itemId)}`, {
    method: "PATCH",
    body: { state },
  });
}

/**
 * Replaces a founder profile wholesale — the backend deliberately has no
 * patch. These fields feed the deterministic eligibility filter, and a
 * half-applied update (citizenship changed, degree level not) is how a
 * founder gets told they are eligible for something they are not. The
 * backend returns what it stored, which is what every other endpoint will
 * serve from now on.
 */
export function putProfile(profile: FounderProfile): Promise<FounderProfile> {
  return request(`/founders/${encodeURIComponent(profile.founder_id)}`, {
    method: "PUT",
    body: profile,
  });
}
