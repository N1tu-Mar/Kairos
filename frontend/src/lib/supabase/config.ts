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
 * Whether login is wired up at all.
 *
 * False leaves the dashboard in its documented single-founder local mode,
 * where the backend is reached with the shared token and there is no sign-in.
 * That mode is for a laptop. It is what `KAIROS_ALLOW_OPEN_API` is to the
 * backend: fine locally, never on anything reachable.
 */
export function authConfigured(): boolean {
  return Boolean(supabaseUrl() && supabaseAnonKey());
}
