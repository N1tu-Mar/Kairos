"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { browserSupabase } from "@/lib/supabase/browser";

/**
 * Email-and-password sign-in.
 *
 * The one client component that touches Supabase directly, because signing in
 * is the moment the session cookie is established and that has to happen in
 * the browser. Everything afterwards reads the session server-side.
 *
 * The error message is deliberately the same for a wrong password and an
 * unknown address. Distinguishing them turns the form into a way to ask
 * whether a given person has an account here.
 */
export function LoginForm({ next }: { next?: string }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);

    const { error: signInError } = await browserSupabase().auth.signInWithPassword({
      email,
      password,
    });

    if (signInError) {
      setPending(false);
      setError("That email and password do not match an account.");
      return;
    }

    // Only ever a path on this origin. `next` arrives in a query string, so
    // handing it to the router unchecked would let a crafted link bounce
    // someone to another site carrying the trust of this one.
    const destination = next && next.startsWith("/") && !next.startsWith("//")
      ? next
      : "/";
    router.replace(destination);
    router.refresh();
  }

  return (
    <form onSubmit={onSubmit} className="mt-6 space-y-4">
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
        disabled={pending}
        className="w-full rounded-md bg-ink px-4 py-2 text-sm font-medium text-surface disabled:opacity-60"
      >
        {pending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
