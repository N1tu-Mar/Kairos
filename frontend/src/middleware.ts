import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { isSupabaseAuth } from "@/lib/auth-mode";

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
 * When auth mode is `local_shared` and Supabase is not configured the
 * dashboard stays in its documented single-founder laptop mode and this
 * middleware steps aside. Production and Vercel preview deploys never take
 * that path: missing public variables become a generic 503 instead of an
 * unauthenticated proxy holding the backend token.
 */

/** Paths that must work while signed out, or signing in is impossible. */
const PUBLIC_PATHS = ["/login", "/auth/callback", "/auth/signout"];
const CSP_HEADER = "Content-Security-Policy";

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );
}

/**
 * A strict policy that still permits the scripts Next generated for this one
 * response. Next reads the nonce from the request CSP and adds it to its
 * framework, hydration and streaming scripts automatically.
 *
 * React's development runtime uses eval for debugging. Production does not,
 * so that exception is deliberately limited to `next dev`.
 */
export function contentSecurityPolicy(nonce: string): string {
  const supabaseOrigin = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim() ?? "";
  const developmentEval =
    process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : "";

  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${developmentEval}`,
    // Next and Tailwind emit inline styles. Scripts remain nonce-restricted.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self'${supabaseOrigin ? ` ${supabaseOrigin} ${supabaseOrigin.replace(/^https:/, "wss:")}` : ""}`,
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "object-src 'none'",
  ].join("; ");
}

function nonceForRequest(): string {
  return Buffer.from(crypto.randomUUID()).toString("base64");
}

function setCsp(response: NextResponse, policy: string): NextResponse {
  response.headers.set(CSP_HEADER, policy);
  return response;
}

export async function middleware(request: NextRequest) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();
  const nonce = nonceForRequest();
  const policy = contentSecurityPolicy(nonce);

  // The request copy is what Next's renderer examines to discover the nonce.
  // Rebuild it after a Supabase cookie refresh so the refreshed Cookie header
  // and the nonce both reach the route.
  const nextResponse = () => {
    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("x-nonce", nonce);
    requestHeaders.set(CSP_HEADER, policy);
    return NextResponse.next({ request: { headers: requestHeaders } });
  };

  // Production / supabase mode must not silently become the laptop posture.
  // A generic 503 names neither Supabase nor the missing variable — those
  // belong in the operator's logs, not in a body a stranger can read.
  if (isSupabaseAuth() && (!url || !key)) {
    return setCsp(
      NextResponse.json({ detail: "service unavailable" }, { status: 503 }),
      policy,
    );
  }

  // Not configured: local single-founder mode, no sign-in, nothing gated.
  if (!url || !key) return setCsp(nextResponse(), policy);

  let response = nextResponse();

  const supabase = createServerClient(url, key, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(toSet) {
        for (const { name, value } of toSet) {
          request.cookies.set(name, value);
        }
        response = nextResponse();
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
    return setCsp(NextResponse.redirect(login), policy);
  }

  return setCsp(response, policy);
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
