/**
 * Who is signed in, and the way out.
 *
 * Deliberately a plain component taking an email rather than one that reads
 * the session itself: the layout that renders it is already doing an
 * authenticated read, and a second `getUser()` per page would be a second
 * round trip to Supabase for something the caller already has.
 *
 * `email` distinguishes three states. `null` is no session at all — local
 * single-founder mode, where a sign-out button would be a control that cannot
 * do anything — and renders nothing. An empty string is a session whose
 * identity carries no address, which is a missing label, not a missing
 * session.
 */
export function AccountBar({ email }: { email: string | null }) {
  if (email === null) return null;

  return (
    <div className="flex items-center gap-3">
      {email ? (
        <span
          className="hidden max-w-[16rem] truncate text-xs text-ink-muted sm:inline"
          title={email}
        >
          {email}
        </span>
      ) : null}
      {/*
        A form, not a link. A GET sign-out can be triggered by any page that
        makes the browser load a URL, which hands a third-party site the
        ability to log somebody out. The route only answers POST.
      */}
      <form action="/auth/signout" method="post">
        <button
          type="submit"
          className="rounded-md px-3 py-1.5 text-sm text-ink-muted transition-colors hover:bg-sunk hover:text-ink"
        >
          Sign out
        </button>
      </form>
    </div>
  );
}
