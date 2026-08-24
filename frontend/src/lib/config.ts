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

export function readTimeoutMs(): number {
  return intEnv("KAIROS_API_TIMEOUT_MS", 10_000);
}

/** A real run does discovery + assessment + drafting. Reads are not a guide. */
export function runTimeoutMs(): number {
  return intEnv("KAIROS_RUN_TIMEOUT_MS", 180_000);
}
