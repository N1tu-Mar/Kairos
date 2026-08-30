/** @type {import('next').NextConfig} */

/**
 * Response headers the browser enforces on our behalf.
 *
 * These were absent, which mattered less when the dashboard held no session:
 * there was nothing to steal from a page that anyone could load anyway. A
 * login changes that. There is now an httpOnly session cookie, and the
 * attacks these headers blunt — clickjacking a signed-in user into clicking
 * something, or a script injected into the page reaching for the session —
 * become worth defending against.
 *
 * Each one, and why it is set the way it is:
 *
 * - **`Content-Security-Policy`** is request-specific and therefore lives in
 *   `src/middleware.ts`, where a fresh nonce can be generated before Next
 *   renders the page. A static CSP here blocked Next's streamed inline
 *   scripts and left every route stuck on its loading skeleton.
 * - **`X-Frame-Options: DENY`** — the same control for browsers that predate
 *   `frame-ancestors`. Redundant on purpose.
 * - **`X-Content-Type-Options: nosniff`** — stops a browser deciding a
 *   response is a script because it looks like one.
 * - **`Referer-Policy`** — a dashboard URL can carry a draft id; there is no
 *   reason to hand that to whatever a user clicks through to.
 * - **`Strict-Transport-Security`** — HTTPS only, once seen. Harmless on
 *   localhost, where browsers ignore it for `http://localhost`.
 *
 * See `src/middleware.ts` for the CSP directives and their rationale.
 */

const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
];

const nextConfig = {
  reactStrictMode: true,
  agentRules: false,
  // The FastAPI URL is server-only on purpose: no NEXT_PUBLIC_ prefix, so it
  // never ships to the browser and the browser never talks to FastAPI direct.
  // Client interactions go through the thin Route Handler proxy in src/app/api.
  //
  // No `eslint` key: Next 16 dropped it, and it was already redundant — the
  // `lint` script scopes ESLint to src/ itself, and CI runs that script, not
  // the build's linting.
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
