import "server-only";

import {
  apiBaseUrl,
  founderId,
  readTimeoutMs,
  runTimeoutMs,
} from "@/lib/config";
import type {
  DraftResponse,
  FounderProfile,
  InboxItem,
  RunReport,
  RunTrigger,
} from "@/lib/types";

/**
 * The single place this app talks to FastAPI.
 *
 * FastAPI stays the source of truth. Nothing here re-implements eligibility,
 * assessment, drafting, gating or persistence — it reads what the Python
 * pipeline already decided and wrote, and offers exactly one write: the
 * manual run trigger that already exists as `POST /founders/{id}/runs`.
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

interface RequestOptions {
  method?: "GET" | "POST";
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
    response = await fetch(url, {
      method,
      headers: body ? { "content-type": "application/json" } : undefined,
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
 * A single historical run. The backend has no `/runs/{run_id}` endpoint, and
 * `list_runs` already returns the complete RunReport — rejections, skips,
 * source failures and notes included — so the detail view selects from the
 * list rather than inventing an endpoint.
 */
export async function getRun(
  runId: string,
  id = founderId(),
  limit = 50,
): Promise<RunReport | null> {
  const runs = await listRuns(id, limit);
  return runs.find((run) => run.run_id === runId) ?? null;
}

export function getDraft(draftId: string): Promise<DraftResponse> {
  return request(`/drafts/${encodeURIComponent(draftId)}`);
}

export function getDraftOrNull(draftId: string): Promise<DraftResponse | null> {
  return optional(getDraft(draftId));
}

// ── The one write ────────────────────────────────────────────────────────────

/**
 * Starts a run now, by hand. This is not a schedule and the UI must not
 * present it as one — see `incomplete.md`, "Scheduled pipeline invocation".
 */
export function triggerRun(
  trigger: RunTrigger,
  id = founderId(),
): Promise<RunReport> {
  return request(`/founders/${encodeURIComponent(id)}/runs`, {
    method: "POST",
    body: trigger,
    timeoutMs: runTimeoutMs(),
  });
}
