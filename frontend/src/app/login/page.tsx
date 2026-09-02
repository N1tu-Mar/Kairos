import { redirect } from "next/navigation";

import { LoginForm } from "@/components/login-form";
import { authConfigured } from "@/lib/supabase/config";
import { currentUser } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * Sign in.
 *
 * Reachable while signed out — one of the few paths `middleware.ts` lets
 * through, since gating it would make signing in impossible.
 *
 * When Supabase is not configured this page has nothing to offer and says so
 * rather than rendering a form that cannot work. That is the local
 * single-founder mode, where the dashboard has no login and the backend is
 * reached with a shared token.
 */
/**
 * Fixed tokens only. `/auth/callback` never puts a provider's own message in
 * this parameter, so nothing attacker-influenced is rendered here.
 */
const ERRORS: Record<string, string> = {
  sign_in_failed:
    "That sign-in did not complete. Try again, or use an email and password.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  if (!authConfigured()) {
    return (
      <main className="mx-auto max-w-lg px-6 py-16">
        <h1 className="text-xl font-medium text-ink">Sign-in is not configured</h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-soft">
          This dashboard is running in local single-founder mode. Set{" "}
          <code className="font-mono text-xs">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
          <code className="font-mono text-xs">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>{" "}
          to turn on accounts.
        </p>
        <p className="mt-3 text-sm leading-relaxed text-ink-muted">
          Local mode has no sign-in and no per-user data. Do not expose it.
        </p>
      </main>
    );
  }

  // Already signed in: nothing to do here. `/briefing` rather than `/`,
  // which is the public landing page and links straight back to this one.
  if (await currentUser()) redirect("/briefing");

  const { next, error } = await searchParams;
  // An unrecognised value renders nothing rather than itself.
  const message = error ? ERRORS[error] : undefined;

  return (
    <main className="mx-auto max-w-sm px-6 py-16">
      <h1 className="text-xl font-medium text-ink">Sign in to Kairos</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-soft">
        Your funding inbox, your drafts, and your run history. Continue with
        Google or GitHub, or create an account with an email address.
      </p>
      {message ? (
        <p role="alert" className="mt-4 text-sm text-alert">
          {message}
        </p>
      ) : null}
      <LoginForm next={next} />
    </main>
  );
}
