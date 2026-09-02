"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { browserSupabase } from "@/lib/supabase/browser";

/**
 * The three ways in: Google, GitHub, and email with a password.
 *
 * The one client component that touches Supabase directly, because signing in
 * is the moment the session cookie is established and that has to happen in
 * the browser. Everything afterwards reads the session server-side.
 *
 * The error message for the password form is deliberately the same for a
 * wrong password and an unknown address. Distinguishing them turns the form
 * into a way to ask whether a given person has an account here.
 */

type Provider = "google" | "github";

const PROVIDERS: { id: Provider; label: string }[] = [
  { id: "google", label: "Continue with Google" },
  { id: "github", label: "Continue with GitHub" },
];

/**
 * `next` as a path on this origin, or null.
 *
 * `next` arrives in a query string, so handing it on unchecked would let a
 * crafted sign-in link bounce someone to another site carrying the trust of
 * this one. A leading `//` is a protocol-relative URL and a leading `\` is
 * treated as a slash by browsers, so both are refused.
 *
 * `/auth/callback` guards this again on the way back. Neither place assumes
 * the other did it.
 */
function safeNext(raw: string | undefined): string | null {
  if (!raw) return null;
  if (!raw.startsWith("/")) return null;
  if (raw.startsWith("//") || raw.startsWith("/\\")) return null;
  return raw;
}

export function LoginForm({ next }: { next?: string }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState<"password" | Provider | null>(null);
  const [error, setError] = useState<string | null>(null);

  const destination = safeNext(next);

  /**
   * Hand the browser to the provider.
   *
   * `redirectTo` is built from `window.location.origin` rather than a
   * configured URL: a preview deploy and production are different origins,
   * and a hardcoded one sends everybody who signs in on a preview to
   * production instead.
   */
  async function signInWith(provider: Provider) {
    setPending(provider);
    setError(null);

    const callback = new URL("/auth/callback", window.location.origin);
    if (destination) callback.searchParams.set("next", destination);

    const { error: oauthError } = await browserSupabase().auth.signInWithOAuth({
      provider,
      options: { redirectTo: callback.toString() },
    });

    // Only reached when the redirect never happened. On success the browser
    // has already left this page.
    if (oauthError) {
      setPending(null);
      setError("Could not start that sign-in. Try again, or use a password.");
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setPending("password");
    setError(null);

    const { error: signInError } = await browserSupabase().auth.signInWithPassword({
      email,
      password,
    });

    if (signInError) {
      setPending(null);
      setError("That email and password do not match an account.");
      return;
    }

    router.replace(destination ?? "/");
    router.refresh();
  }

  const busy = pending !== null;

  return (
    <div className="mt-6">
      <div className="space-y-3">
        {PROVIDERS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => signInWith(id)}
            disabled={busy}
            className="w-full rounded-md border border-rule bg-surface px-4 py-2 text-sm font-medium text-ink disabled:opacity-60"
          >
            {pending === id ? "Redirecting…" : label}
          </button>
        ))}
      </div>

      <div className="my-6 flex items-center gap-3" aria-hidden="true">
        <span className="h-px flex-1 bg-rule" />
        <span className="text-xs text-ink-muted">or</span>
        <span className="h-px flex-1 bg-rule" />
      </div>

      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label htmlFor="email" className="block text-sm font-medium text-ink">
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 w-full rounded-md border border-rule bg-surface px-3 py-2 text-sm text-ink"
          />
        </div>

        <div>
          <label htmlFor="password" className="block text-sm font-medium text-ink">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 w-full rounded-md border border-rule bg-surface px-3 py-2 text-sm text-ink"
          />
        </div>

        {error ? (
          <p role="alert" className="text-sm text-alert">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-md bg-ink px-4 py-2 text-sm font-medium text-surface disabled:opacity-60"
        >
          {pending === "password" ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
