import { afterEach, describe, expect, it, vi } from "vitest";

import {
  authConfigured,
  supabaseUrlProblem,
} from "@/lib/supabase/config";

/**
 * Telling a misconfigured sign-in apart from a deliberately absent one.
 *
 * These two states look identical to `Boolean(url && key)` and mean opposite
 * things. Empty is local single-founder mode, which is a documented posture.
 * A publishable key pasted into the URL slot is a mistake, and the way it used
 * to surface was a dashboard that quietly ran with no sign-in at all, showing
 * the synthetic demo founder to anyone who loaded it.
 *
 * The same reasoning as `apiBaseUrlProblem` in `lib/config.ts`: a
 * configuration mistake and an absent configuration are different problems and
 * must not produce the same silence.
 */

afterEach(() => {
  vi.unstubAllEnvs();
});

function configure(url: string, key = "sb_publishable_abc123") {
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", url);
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", key);
}

describe("an unusable project URL", () => {
  it.each([
    ["a publishable key", "sb_publishable_JKB547zDuwugA4bptFhTkQ"],
    ["a secret key", "sb_secret_abcdefghijklmnop"],
    ["a legacy anon JWT", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def"],
  ])("names %s pasted into the URL slot", (_name, value) => {
    configure(value);

    expect(supabaseUrlProblem()).toMatch(/key, not a URL/i);
  });

  it("rejects something that is not a URL at all", () => {
    configure("my-project");

    expect(supabaseUrlProblem()).toBeTruthy();
  });

  it("rejects a scheme the browser will not fetch", () => {
    configure("ftp://abcdefghijklm.supabase.co");

    expect(supabaseUrlProblem()).toMatch(/https/i);
  });

  it("never returns the value itself", () => {
    configure("sb_publishable_JKB547zDuwugA4bptFhTkQ");

    // The problem string reaches a rendered page. Echoing the value there
    // would put a credential in front of whoever loaded it.
    expect(supabaseUrlProblem()).not.toContain("JKB547z");
  });
});

describe("a usable project URL", () => {
  it("accepts the project URL Supabase hands out", () => {
    configure("https://abcdefghijklm.supabase.co");

    expect(supabaseUrlProblem()).toBeNull();
  });

  it("accepts a self-hosted origin on http for local work", () => {
    configure("http://localhost:54321");

    expect(supabaseUrlProblem()).toBeNull();
  });

  it("says nothing when sign-in is deliberately switched off", () => {
    configure("", "");

    // Absent is a posture, not a mistake. Local single-founder mode.
    expect(supabaseUrlProblem()).toBeNull();
  });
});

describe("what counts as configured", () => {
  it("is true for a real project URL and key", () => {
    configure("https://abcdefghijklm.supabase.co");

    expect(authConfigured()).toBe(true);
  });

  it("is false when the URL slot holds a key", () => {
    configure("sb_publishable_JKB547zDuwugA4bptFhTkQ");

    // Reporting this as configured is what produced a login page whose
    // buttons all failed inside the Supabase client.
    expect(authConfigured()).toBe(false);
  });

  it("is false when only half of it is filled in", () => {
    configure("https://abcdefghijklm.supabase.co", "");

    expect(authConfigured()).toBe(false);
  });
});
