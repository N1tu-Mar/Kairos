/**
 * Whether this dashboard has a real identity provider behind it, and how to reach it.
 *
 * Both values are `NEXT_PUBLIC_` on purpose, and it is worth being precise
 * about why that is safe here when it is not elsewhere. The Supabase URL and
 * the anon key are *designed* to ship to browsers — the anon key is a public
 * identifier that carries no privilege of its own; every request it makes is
 * still checked by Supabase against the signed-in user. It is not a secret,
 * and it is not the backend's bearer token, which stays server-only.
 *
 * The service-role key is the one that must never appear with this prefix.
 * It is not read anywhere in this app.
 */

/** The project URL, e.g. `https://abcdefghijklm.supabase.co`. */
export function supabaseUrl(): string {
  return process.env.NEXT_PUBLIC_SUPABASE_URL?.trim() ?? "";
}

/** The publishable anon key. Public by design; never the service-role key. */
export function supabaseAnonKey(): string {
  return process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim() ?? "";
}

/**
 * Key prefixes, so a credential in the URL slot is named as one.
 *
 * `sb_publishable_` and `sb_secret_` are the current Supabase key formats and
 * `eyJ` is the base64 opening of a legacy anon JWT. Recognising them is not
 * validation for its own sake — pasting the key into both slots is the single
 * easiest mistake to make here, because the dashboard shows the two values
 * next to each other.
 */
const KEY_PREFIXES = ["sb_publishable_", "sb_secret_", "eyJ"];

/**
 * Why `NEXT_PUBLIC_SUPABASE_URL` is unusable, or null when it is fine.
 *
 * An empty value is not a problem: that is local single-founder mode, a
 * documented posture. This is about the values that look configured and are
 * not, which used to surface as `authConfigured()` returning true and every
 * button on the login page failing somewhere inside the Supabase client.
 *
 * Returns a reason, never the value. The reason is rendered on a page, and a
 * key echoed there is a key shown to whoever loaded it.
 */
export function supabaseUrlProblem(): string | null {
  const raw = supabaseUrl();
  if (!raw) return null;

  if (KEY_PREFIXES.some((prefix) => raw.startsWith(prefix))) {
    return "NEXT_PUBLIC_SUPABASE_URL holds a key, not a URL. It wants the project URL — https://<project-ref>.supabase.co — and the key belongs in NEXT_PUBLIC_SUPABASE_ANON_KEY.";
  }

  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return "NEXT_PUBLIC_SUPABASE_URL is not a valid URL. It wants https://<project-ref>.supabase.co.";
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    return "NEXT_PUBLIC_SUPABASE_URL has to start with https:// (or http:// for a local Supabase).";
  }
  return null;
}

/**
 * Whether login is wired up at all.
 *
 * False leaves the dashboard in its documented single-founder local mode,
 * where the backend is reached with the shared token and there is no sign-in.
 * That mode is for a laptop. It is what `KAIROS_ALLOW_OPEN_API` is to the
 * backend: fine locally, never on anything reachable.
 *
 * A malformed URL counts as not configured rather than as configured-and-
 * broken. The two produce very different failures — one is a login page that
 * explains itself, the other is a client that throws on every call — and only
 * the first is any use to whoever has to fix it.
 */
export function authConfigured(): boolean {
  return Boolean(supabaseUrl() && supabaseAnonKey()) && !supabaseUrlProblem();
}
