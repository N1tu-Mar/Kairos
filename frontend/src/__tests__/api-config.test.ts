import { afterEach, describe, expect, it, vi } from "vitest";

import { apiBaseUrlProblem } from "@/lib/config";

/**
 * A broken `KAIROS_API_URL` must not be reported as a stopped backend.
 *
 * The failure that produced these tests: a line in `frontend/.env.local` was
 * pasted onto itself, so `KAIROS_API_URL` held the string
 * `KAIROS_API_URL=http://127.0.0.1:8000`. Dotenv treats everything after the
 * first `=` as the value, so this is a valid assignment of an invalid URL.
 * `fetch` then failed the same way it fails when nothing is listening, and the
 * dashboard said "Is the FastAPI backend running?" about a backend that was
 * answering 200 on `/ready`. Ten minutes went into restarting a healthy server.
 */
describe("apiBaseUrlProblem", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("passes a normal URL", () => {
    vi.stubEnv("KAIROS_API_URL", "http://127.0.0.1:8000");
    expect(apiBaseUrlProblem()).toBeNull();
  });

  it("passes an https URL with a path", () => {
    vi.stubEnv("KAIROS_API_URL", "https://kairos.example.com/api/");
    expect(apiBaseUrlProblem()).toBeNull();
  });

  it("passes when the variable is unset, because the default is valid", () => {
    vi.stubEnv("KAIROS_API_URL", "");
    expect(apiBaseUrlProblem()).toBeNull();
  });

  it("names the self-pasted line specifically, because that is the actual fix", () => {
    vi.stubEnv("KAIROS_API_URL", "KAIROS_API_URL=http://127.0.0.1:8000");
    expect(apiBaseUrlProblem()).toMatch(/pasted onto itself/i);
  });

  it("rejects a value that is not a URL at all", () => {
    vi.stubEnv("KAIROS_API_URL", "127.0.0.1:8000");
    expect(apiBaseUrlProblem()).toMatch(/not a valid URL/i);
  });

  it("rejects a scheme that fetch cannot use", () => {
    vi.stubEnv("KAIROS_API_URL", "ftp://127.0.0.1:8000");
    expect(apiBaseUrlProblem()).toMatch(/http:\/\/ or https:\/\//);
  });

  it("never puts the configured address in the reason", () => {
    vi.stubEnv("KAIROS_API_URL", "gopher://secret-internal-host.example:9999");
    const problem = apiBaseUrlProblem();
    expect(problem).not.toBeNull();
    expect(problem).not.toContain("secret-internal-host");
    expect(problem).not.toContain("9999");
  });
});
