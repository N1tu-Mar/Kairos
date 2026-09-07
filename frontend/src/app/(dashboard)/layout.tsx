import Link from "next/link";

import { AccountBar } from "@/components/account-bar";
import { SiteNav } from "@/components/site-nav";
import { authConfigured } from "@/lib/supabase/config";
import { currentUser } from "@/lib/supabase/server";

/**
 * The signed-in chrome: masthead, primary navigation, account, footnotes.
 *
 * Scoped to the dashboard route group so the landing page and the sign-in
 * screen do not inherit navigation to places a signed-out visitor cannot go.
 *
 * The session read here is for the label on the account bar, not for the
 * gate. `middleware.ts` is the gate, and it has already turned an anonymous
 * request away before this layout renders — a check in a layout would run
 * after the page beneath it had already been allowed to start.
 */
export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // `null` in local single-founder mode: no sign-in exists, so there is
  // nothing to sign out of and `AccountBar` renders nothing.
  const email = authConfigured() ? ((await currentUser())?.email ?? "") : null;

  return (
    <>
      <div className="border-b border-rule">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-3 px-5 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <Link href="/briefing" className="flex items-baseline gap-2.5">
            <span className="font-serif text-lg tracking-tight text-ink">
              Kairos
            </span>
            <span className="hidden text-xs text-ink-muted sm:inline">
              the opportune moment
            </span>
          </Link>
          <div className="flex items-center gap-2">
            <SiteNav />
            <AccountBar email={email} />
          </div>
        </div>
      </div>

      <main id="main">{children}</main>

      <footer className="border-t border-rule">
        <div className="mx-auto w-full max-w-5xl px-5 py-8 text-xs leading-relaxed text-ink-muted sm:px-8">
          <p>
            Kairos prepares applications. It never submits one, because
            submission is a decision and a person makes it.
          </p>
          <p className="mt-2">
            Rows marked <span className="font-mono">[DEMO]</span> are synthetic
            records from the demo catalog. They are not real funding
            opportunities.
          </p>
        </div>
      </footer>
    </>
  );
}
