"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { browserSupabase } from "@/lib/supabase/browser";

/**
 * The ways in: Google, GitHub, and an email address with a password.
 *
 * The one client component that touches Supabase directly, because signing in
 * is the moment the session cookie is established and that has to happen in
 * the browser. Everything afterwards reads the session server-side.
 *
 * Two things this form deliberately refuses to tell a stranger. The sign-in
 * error is the same for a wrong password and an unknown address, and the
 * sign-up error is the same for a taken address and a rejected password.
 * Either distinction turns the form into a way to ask whether a given person
 * has an account here.
 */

type Provider = "google" | "github";
type Mode = "signin" | "signup";

const PROVIDERS: { id: Provider; label: string }[] = [
  { id: "google", label: "Continue with Google" },
  { id: "github", label: "Continue with GitHub" },
];

/** Where a completed sign-in lands when nothing else was asked for. */
const HOME = "/briefing";

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
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState<"credentials" | Provider | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const destination = safeNext(next);

  /**
   * This origin's `/auth/callback`, carrying `next` when there is a safe one.
   *
   * Built from `window.location.origin` rather than a configured URL: a
   * preview deploy and production are different origins, and a hardcoded one
   * sends everybody who signs in on a preview to production instead.
   *
   * Used for both the provider round trip and the sign-up confirmation email,
   * because both come back to the same place holding a code to exchange.
   */
  function callbackUrl(): string {
    const callback = new URL("/auth/callback", window.location.origin);
    if (destination) callback.searchParams.set("next", destination);
    return callback.toString();
  }

  /** Move between signing in and signing up, clearing whatever was on screen. */
  function switchTo(next: Mode) {
    setMode(next);
    setError(null);
    setNotice(null);
  }

  /** Hand the browser to the provider. */
  async function signInWith(provider: Provider) {
    setPending(provider);
    setError(null);
    setNotice(null);

    const { error: oauthError } = await browserSupabase().auth.signInWithOAuth({
      provider,
      options: { redirectTo: callbackUrl() },
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
    setPending("credentials");
    setError(null);
    setNotice(null);

    const auth = browserSupabase().auth;

    if (mode === "signup") {
      const { data, error: signUpError } = await auth.signUp({
        email,
        password,
        options: { emailRedirectTo: callbackUrl() },
      });

      if (signUpError) {
        setPending(null);
        setError(
          "That account could not be created. Check the address and try a longer password.",
        );
        return;
      }

      // A project with email confirmation on returns a user and no session.
      // Routing to the briefing here would land on the middleware's redirect
      // back to /login, which reads as the sign-up having failed.
      if (!data?.session) {
        setPending(null);
        setNotice(
          "Check your email for a confirmation link. Opening it finishes the sign-up.",
        );
        return;
      }
    } else {
      const { error: signInError } = await auth.signInWithPassword({
        email,
        password,
      });

      if (signInError) {
        setPending(null);
        setError("That email and password do not match an account.");
        return;
      }
    }

    router.replace(destination ?? HOME);
    router.refresh();
  }

  const busy = pending !== null;
  const submitLabel = mode === "signup" ? "Create account" : "Sign in";
  const workingLabel = mode === "signup" ? "Creating account…" : "Signing in…";

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

      <p className="mt-3 text-xs leading-relaxed text-ink-muted">
        Signing in with Google or GitHub creates your Kairos account the first
        time. There is nothing else to fill in.
      </p>

      <div className="my-6 flex items-center gap-3" aria-hidden="true">
        <span className="h-px flex-1 bg-rule" />
        <span className="text-xs text-ink-muted">or</span>
        <span className="h-px flex-1 bg-rule" />
      </div>

      {/*
        A tablist rather than two buttons, so the submit control below stays
        the only thing on this form called "Sign in".
      */}
      <div
        role="tablist"
        aria-label="Account access"
        className="flex rounded-md border border-rule p-0.5"
      >
        {(
          [
            ["signin", "Sign in with email"],
            ["signup", "Create account"],
          ] as [Mode, string][]
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={mode === value}
            onClick={() => switchTo(value)}
            className={`flex-1 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
              mode === value
                ? "bg-accent-soft text-accent"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <form onSubmit={onSubmit} className="mt-4 space-y-4">
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
            // A password manager should offer to generate one when the form is
            // creating an account, and to fill the saved one when it is not.
            autoComplete={
              mode === "signup" ? "new-password" : "current-password"
            }
            required
            minLength={mode === "signup" ? 8 : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 w-full rounded-md border border-rule bg-surface px-3 py-2 text-sm text-ink"
          />
          {mode === "signup" ? (
            <p className="mt-1 text-xs text-ink-muted">
              At least 8 characters.
            </p>
          ) : null}
        </div>

        {error ? (
          <p role="alert" className="text-sm text-alert">
            {error}
          </p>
        ) : null}

        {notice ? (
          <p role="status" className="text-sm text-ink-soft">
            {notice}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-md bg-ink px-4 py-2 text-sm font-medium text-surface disabled:opacity-60"
        >
          {pending === "credentials" ? workingLabel : submitLabel}
        </button>
      </form>
    </div>
  );
}
