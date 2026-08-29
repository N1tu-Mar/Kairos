import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/**
 * The lock on the front door.
 *
 * Until this existed, every page and every proxy route in `src/app/api` was
 * reachable by anyone who knew the URL. That was not a missing feature so
 * much as a structural hole: the proxy holds the backend's credential and
 * attaches it to whatever it is asked to do, so an unauthenticated visitor
 * could read every draft and trigger runs that cost money. FastAPI's own auth
 * never saw an anonymous request, because the proxy made it on their behalf.
 *
 * Two jobs, in this order:
 *
 * 1.  **Refresh the session.** Supabase access tokens are short-lived. A
 *     Server Component cannot write cookies, so the refresh has to happen
 *     here or a signed-in user is signed out an hour later.
 * 2.  **Redirect anyone without a session** to `/login`, except for the
 *     handful of paths that must stay reachable to sign in at all.
 *
 * When Supabase is not configured the dashboard stays in its documented
 * single-founder local mode and this middleware steps aside. That mode is for
 * a laptop — the same posture, and the same warning, as the backend's
 * `KAIROS_ALLOW_OPEN_API`.
 */

/** Paths that must work while signed out, or signing in is impossible. */
const PUBLIC_PATHS = ["/login", "/auth/callback", "/auth/signout"];

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );
}

export async function middleware(request: NextRequest) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();

  // Not configured: local single-founder mode, no sign-in, nothing gated.
  if (!url || !key) return NextResponse.next();

  let response = NextResponse.next({ request });

  const supabase = createServerClient(url, key, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(toSet) {
        for (const { name, value } of toSet) {
          request.cookies.set(name, value);
        }
        response = NextResponse.next({ request });
        for (const { name, value, options } of toSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  // `getUser`, not `getSession`: this revalidates the token with the auth
  // server instead of trusting the cookie. A gate that trusts a cookie it was
  // handed is not a gate. It also performs the refresh, which is why the
  // call happens even on public paths.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user && !isPublic(request.nextUrl.pathname)) {
    const login = request.nextUrl.clone();
    login.pathname = "/login";
    // Where they were headed, so signing in does not dump them on the
    // dashboard root. Only ever a path on this origin — `next` is read back
    // through `URL` parsing in the login route, so an absolute URL here
    // cannot turn the redirect into an open redirect off-site.
    login.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(login);
  }

  return response;
}

export const config = {
  /**
   * Everything except Next's own static output and the favicon.
   *
   * Deliberately an exclusion list rather than an inclusion one: a new route
   * is protected the moment it exists, instead of being protected once
   * somebody remembers to add it here. Getting that default backwards is how
   * a page ships unguarded.
   */
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
