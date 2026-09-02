import { NextResponse } from "next/server";

import { supabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * Where Google and GitHub send someone back to.
 *
 * `middleware.ts` has listed `/auth/callback` among its public paths since the
 * gate was built, and the route itself did not exist — so an OAuth sign-in
 * could start and never finish. This completes it: the provider returns a
 * one-time `code`, and this exchanges it for the session cookies that every
 * later request is checked against.
 *
 * The exchange happens server-side, which is the point of using the PKCE flow
 * rather than reading a token out of a URL fragment in the browser. The
 * session lands in httpOnly cookies that no script on the page can read.
 *
 * Every failure ends at `/login?error=sign_in_failed` rather than a 500. This
 * is the one route a person is standing on mid-sign-in, and the useful answer
 * to "the code was stale" or "Supabase is down" is the same: the form they
 * came from, with a sentence they can act on.
 */

/** Fixed tokens, so nothing a provider says reaches a URL a person reads. */
const SIGN_IN_FAILED = "sign_in_failed";

/**
 * `next` as a path on this origin, or `/`.
 *
 * The same guard as `login-form.tsx`, for the same reason: `next` arrives in a
 * query string, so a crafted sign-in link would otherwise bounce somebody to
 * another site carrying the trust of this one. A leading `//` is a
 * protocol-relative URL and a leading `\` is treated as a slash by browsers,
 * so both are rejected alongside anything that is not a path at all.
 */
function safeNext(raw: string | null): string {
  if (!raw) return "/";
  if (!raw.startsWith("/")) return "/";
  if (raw.startsWith("//") || raw.startsWith("/\\")) return "/";
  return raw;
}

function backToLogin(request: Request, reason: string): NextResponse {
  const login = new URL("/login", request.url);
  login.searchParams.set("error", reason);
  return NextResponse.redirect(login, { status: 303 });
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");

  // The provider refused, or the person declined the consent screen. There is
  // no code to exchange and nothing went wrong on our side.
  if (url.searchParams.get("error") || !code) {
    return backToLogin(request, SIGN_IN_FAILED);
  }

  try {
    const supabase = await supabaseServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) return backToLogin(request, SIGN_IN_FAILED);
  } catch {
    // Supabase unreachable, or a malformed response. Same answer: the login
    // page, not an unhandled 500 in the middle of signing in.
    return backToLogin(request, SIGN_IN_FAILED);
  }

  // 303, so a browser follows with GET regardless of how it arrived here.
  return NextResponse.redirect(
    new URL(safeNext(url.searchParams.get("next")), request.url),
    { status: 303 },
  );
}
