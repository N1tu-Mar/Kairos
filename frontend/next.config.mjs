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
 * - **`Content-Security-Policy`** — the one doing real work. `'self'` for
 *   scripts, plus the `connect-src` entries Supabase auth needs. Tailwind and
 *   Next both inline styles, hence `'unsafe-inline'` for styles only; scripts
 *   get no such allowance, which is where it counts. `frame-ancestors 'none'`
 *   is the modern clickjacking control.
 * - **`X-Frame-Options: DENY`** — the same control for browsers that predate
 *   `frame-ancestors`. Redundant on purpose.
 * - **`X-Content-Type-Options: nosniff`** — stops a browser deciding a
 *   response is a script because it looks like one.
 * - **`Referer-Policy`** — a dashboard URL can carry a draft id; there is no
 *   reason to hand that to whatever a user clicks through to.
 * - **`Strict-Transport-Security`** — HTTPS only, once seen. Harmless on
 *   localhost, where browsers ignore it for `http://localhost`.
 *
 * `NEXT_PUBLIC_SUPABASE_URL` is interpolated into `connect-src` rather than
 * wildcarded, so the page may reach *our* Supabase project and no other.
 */

const supabaseOrigin = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim() ?? "";

const csp = [
  "default-src 'self'",
  "script-src 'self'",
  // Next and Tailwind both emit inline styles. Styles cannot exfiltrate the
  // way a script can, and scripts get no equivalent allowance.
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self'${supabaseOrigin ? ` ${supabaseOrigin} ${supabaseOrigin.replace(/^https:/, "wss:")}` : ""}`,
  "form-action 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
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
