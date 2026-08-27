import "server-only";

/**
 * Server-side configuration. Read lazily so a missing value surfaces as a
 * rendered error state in the UI rather than a build-time crash.
 */

function env(key: string, fallback: string): string {
  const value = process.env[key]?.trim();
  return value ? value : fallback;
}

function intEnv(key: string, fallback: number): number {
  const raw = process.env[key]?.trim();
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function apiBaseUrl(): string {
  return env("KAIROS_API_URL", "http://127.0.0.1:8000").replace(/\/+$/, "");
}

/** The dashboard is single-founder. There is no auth in this repository. */
export function founderId(): string {
  return env("KAIROS_FOUNDER_ID", "founder_demo");
}

/**
 * Bearer token the backend expects when `KAIROS_API_TOKEN` is set there.
 * Server-only, like everything in this module: the browser talks to the
 * Route Handlers, never to FastAPI, so the credential never ships to it.
 * Empty means the backend is running open (localhost demo).
 */
export function apiToken(): string {
  return env("KAIROS_API_TOKEN", "");
}

/**
 * Every call this app makes is now short. Starting a run creates a job and
 * returns; the run's own wall-clock ceiling is the backend's
 * `KAIROS_RUN_TIMEOUT_S`, not a socket this app holds open.
 */
export function readTimeoutMs(): number {
  return intEnv("KAIROS_API_TIMEOUT_MS", 10_000);
}
