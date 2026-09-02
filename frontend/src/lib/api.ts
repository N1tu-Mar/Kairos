import "server-only";

import { cache } from "react";

import {
  apiBaseUrl,
  apiBaseUrlProblem,
  apiToken,
  founderId,
  readTimeoutMs,
} from "@/lib/config";
import { isSupabaseAuth } from "@/lib/auth-mode";
import { currentAccessToken } from "@/lib/supabase/server";
import type {
  DraftResponse,
  EligibilityAnswerValue,
  EligibilityQuestion,
  FounderProfile,
  Identity,
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
  | "malformed"
  // The dashboard's own configuration is wrong, so no request was attempted.
  // Distinct from `unreachable`, which means a request was made and failed.
  | "misconfigured"
  // Supabase mode with no usable session. Must not fall through to the
  // shared backend token — that is the production hole this kind exists
  // to keep closed.
  | "unauthorized"
  // Signed in, but granted no founder. A real state, not a fault: it is what
  // a verified person looks like before anyone linked them. Kept distinct
  // from `unauthorized` because the answer is "ask for access", not
  // "sign in" — treating it as the latter loops the dashboard through /login.
  | "no_founder";

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

  /**
   * One line the UI can show a human without leaking a stack trace.
   *
   * It also must not name the deployment. This used to interpolate
   * `apiBaseUrl()` into the unreachable case, which meant an unauthenticated
   * stranger could ask the dashboard where its backend lives by taking the
   * backend down — or just by waiting for it to be down. The address helps
   * nobody who cannot already read the environment, and the operator now
   * gets it in the server log instead (`src/lib/errors.ts`).
   */
  get userMessage(): string {
    switch (this.kind) {
      case "misconfigured":
        // The one case where naming the variable is the entire fix. It names
        // the setting, never its value.
        return `${this.message} Fix it in frontend/.env.local, then restart the dev server.`;
      case "unauthorized":
        return "Sign in to continue.";
      case "no_founder":
        return "This account has no workspace yet. Ask an operator for access.";
      case "unreachable":
        return "Could not reach the Kairos API. Is the FastAPI backend running?";
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
    // The dashboard is broken, not the backend. 500, not 502.
    case "misconfigured":
      return 500;
    case "unauthorized":
      return 401;
    // Authenticated, and deliberately granted nothing. 403, not 401: signing
    // in again is not the fix.
    case "no_founder":
      return 403;
    default:
      return error.status ?? 502;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT";
  body?: unknown;
  timeoutMs?: number;
}

/**
 * The one fetch in this app. Every read and write goes through here.
 *
 * Three things it guarantees that individual callers must not re-implement:
 *
 * - The bearer token is attached **server-side only**. This module is
 *   `server-only`, so the credential never reaches the browser.
 * - `cache: "no-store"`. Every view reflects live pipeline state, and a
 *   cached render of a run that has since finished is a lie.
 * - Every failure becomes an `ApiError` with a `kind` — timeout,
 *   unreachable, not_found, http, malformed. Callers branch on the kind
 *   rather than on a message.
 *
 * The abort timer is cleared in `finally`, so a slow-but-successful
 * response does not leave a pending timeout behind.
 */
async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, timeoutMs = readTimeoutMs() } = options;

  // Checked before the fetch, not after it fails. A bad base URL makes every
  // request fail in a way indistinguishable from a stopped backend, and the
  // reader then goes and restarts a healthy one.
  const problem = apiBaseUrlProblem();
  if (problem) throw new ApiError("misconfigured", problem, path);

  const url = `${apiBaseUrl()}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const headers: Record<string, string> = {};
  if (body) headers["content-type"] = "application/json";
  // Supabase mode: the *user's* access token, or no request at all.
  // Falling back to KAIROS_API_TOKEN here is how an unauthenticated
  // visitor used to trigger paid runs — the proxy held the backend
  // credential and attached it on their behalf.
  //
  // This runs *before* the fetch try/catch so an ApiError is not swallowed
  // into "unreachable".
  const sessionToken = await currentAccessToken();
  if (isSupabaseAuth()) {
    if (!sessionToken) {
      throw new ApiError(
        "unauthorized",
        "A signed-in session is required",
        path,
        401,
      );
    }
    headers.authorization = `Bearer ${sessionToken}`;
  } else {
    const token = sessionToken || apiToken();
    if (token) headers.authorization = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: Object.keys(headers).length > 0 ? headers : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      // Every view reflects live pipeline state; a stale render is a lie.
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof ApiError) throw error;
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

/**
 * Backend liveness. Does not check the database — see `/ready` for that.
 */
export function getHealth(): Promise<{ status: string }> {
  return request("/health");
}

/**
 * Who this request's session is, and which founder it may render.
 *
 * `cache` is React's per-request memo, not a time-based one: every Server
 * Component in a single render shares one `/me` call, and the next request
 * asks again. Memberships change, and caching across requests is how a
 * revoked person keeps a working dashboard.
 */
export const getIdentity = cache(
  async (): Promise<Identity> => request<Identity>("/me"),
);

/**
 * The founder id to render for this session.
 *
 * The whole reason this is a function rather than `KAIROS_FOUNDER_ID`. That
 * variable names one founder for the entire deployment — correct on a laptop,
 * and silently wrong the moment two people can sign in: both are shown the
 * same inbox, each believing it is theirs.
 *
 * In `local_shared` mode there is no session to ask about, so the variable is
 * still the answer, and `/me` is not called — a laptop running with no
 * credential would only get a 401 from it.
 *
 * A session that owns no founder raises rather than falling back to the
 * variable. Falling back would hand whoever completed a sign-in the demo
 * founder's inbox, which is the tenancy version of the shared-token hole.
 */
export const currentFounderId = cache(async (): Promise<string> => {
  if (!isSupabaseAuth()) return founderId();
  const identity = await getIdentity();
  if (!identity.founder_id) {
    throw new ApiError("no_founder", "This session owns no founder", "/me", 403);
  }
  return identity.founder_id;
});

/**
 * The founder profile. Throws `ApiError('not_found')` when none has been saved.
 */
export async function getProfile(id?: string): Promise<FounderProfile> {
  const target = id ?? (await currentFounderId());
  return request(`/founders/${encodeURIComponent(target)}`);
}

/**
 * As {@link getProfile}, but `null` on a 404 — the first-boot state, not an error.
 */
export async function getProfileOrNull(id?: string): Promise<FounderProfile | null> {
  const target = id ?? (await currentFounderId());
  return optional(getProfile(target));
}

/**
 * Surfaced opportunities, newest first.
 *
 * `includePassive=false` hides overflow items. Note the backend applies its
 * row limit before that filter, so the false case can return fewer rows
 * than the limit while more non-passive items exist.
 */
export async function getInbox(
  id?: string,
  includePassive = true,
): Promise<InboxItem[]> {
  const target = id ?? (await currentFounderId());
  const query = includePassive ? "" : "?include_passive=false";
  return request(`/founders/${encodeURIComponent(target)}/inbox${query}`);
}

export async function listEligibilityQuestions(
  status: "pending" | "answered" | "all" = "pending",
  id?: string,
): Promise<EligibilityQuestion[]> {
  const target = id ?? (await currentFounderId());
  return request(
    `/founders/${encodeURIComponent(target)}/eligibility-questions?status=${status}`,
  );
}

/**
 * Recent run reports, newest first. Capped by `limit`; {@link getRun} reaches older ones.
 */
export async function listRuns(id?: string, limit = 20): Promise<RunReport[]> {
  const target = id ?? (await currentFounderId());
  return request(`/founders/${encodeURIComponent(target)}/runs?limit=${limit}`);
}

/**
 * `null` means "no run has ever been recorded", which is a first-boot state,
 * not an error. A run that scanned and surfaced nothing is a *successful*
 * run and comes back as a normal RunReport.
 */
export async function getLatestRun(id?: string): Promise<RunReport | null> {
  const target = id ?? (await currentFounderId());
  return optional(request<RunReport>(`/founders/${encodeURIComponent(target)}/runs/latest`));
}

/**
 * A single historical run, however old. `list_runs` is capped, so this reads
 * `GET /founders/{id}/runs/{run_id}` — scoped to the founder so a mistyped id
 * 404s instead of quietly resolving to someone else's run.
 */
export async function getRun(
  runId: string,
  id?: string,
): Promise<RunReport | null> {
  const target = id ?? (await currentFounderId());
  return optional(
    request<RunReport>(
      `/founders/${encodeURIComponent(target)}/runs/${encodeURIComponent(runId)}`,
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

/**
 * Search-discovered candidate rows per lane — a review queue, not the live catalog.
 *
 * These rows carry their own review status and have not been through
 * verification; nothing here has reached a founder.
 */
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
export async function listDrafts(
  id?: string,
  opportunityId?: string,
): Promise<DraftResponse[]> {
  const target = id ?? (await currentFounderId());
  const query = opportunityId
    ? `?opportunity_id=${encodeURIComponent(opportunityId)}`
    : "";
  return request(`/founders/${encodeURIComponent(target)}/drafts${query}`);
}

/**
 * One draft with its counts. Throws on a draft that does not exist or is not this founder's.
 */
export function getDraft(draftId: string): Promise<DraftResponse> {
  return request(`/drafts/${encodeURIComponent(draftId)}`);
}

/**
 * As {@link getDraft}, but `null` rather than throwing on a 404.
 */
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
export async function triggerRun(
  trigger: RunTrigger,
  id?: string,
): Promise<RunJob> {
  const target = id ?? (await currentFounderId());
  return request(`/founders/${encodeURIComponent(target)}/runs`, {
    method: "POST",
    body: trigger,
    // A short timeout now: this call only creates a job. The run's own
    // ceiling lives in the backend (KAIROS_RUN_TIMEOUT_S), not in a socket.
    timeoutMs: readTimeoutMs(),
  });
}

/** One job plus its report once the run has produced one. The poll target. */
export async function getJobStatus(
  jobId: string,
  id?: string,
): Promise<JobStatusResponse> {
  const target = id ?? (await currentFounderId());
  return request(
    `/founders/${encodeURIComponent(target)}/jobs/${encodeURIComponent(jobId)}`,
  );
}

/**
 * Recent run jobs, newest first — in-flight and finished alike.
 */
export async function listJobs(id?: string, limit = 20): Promise<RunJob[]> {
  const target = id ?? (await currentFounderId());
  return request(`/founders/${encodeURIComponent(target)}/jobs?limit=${limit}`);
}

/**
 * Asks the backend to stop a running job. Cooperative — the run stops at its
 * next await point. What it already persisted stays persisted.
 */
export async function cancelJob(
  jobId: string,
  id?: string,
): Promise<{ cancelled: boolean; status: string }> {
  const target = id ?? (await currentFounderId());
  return request(
    `/founders/${encodeURIComponent(target)}/jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST" },
  );
}

/**
 * Invocations that failed to start or finish, newest first. Sanitised
 * server-side — no credentials, no prompts, no stack traces. This is how a
 * founder learns that last night's scheduled run never ran.
 */
export async function listSchedulerFailures(
  id?: string,
  limit = 5,
): Promise<SchedulerFailure[]> {
  const target = id ?? (await currentFounderId());
  return request(
    `/founders/${encodeURIComponent(target)}/scheduler/failures?limit=${limit}`,
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

export async function answerEligibilityQuestion(
  questionId: string,
  answer: EligibilityAnswerValue,
  id?: string,
): Promise<EligibilityQuestion> {
  const target = id ?? (await currentFounderId());
  return request(
    `/founders/${encodeURIComponent(target)}/eligibility-questions/${encodeURIComponent(questionId)}/answer`,
    { method: "PUT", body: { answer } },
  );
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
