/**
 * Which identity story this dashboard is telling.
 *
 * `local_shared` is a laptop: no sign-in, the proxy may attach
 * `KAIROS_API_TOKEN`. `supabase` is a deployment: every backend call must
 * carry the signed-in user's access token, and missing Supabase public
 * variables are a 503 rather than a silent fall-through into laptop mode.
 *
 * A Vercel production or preview deploy is always `supabase`, even if
 * someone copied a local `.env` that says otherwise. That is the whole
 * point of this module — the hole was "forgot the public vars, so the
 * shared backend token acted for whoever loaded the page".
 */

export type AuthMode = "local_shared" | "supabase";

function vercelDeployed(): boolean {
  const env = process.env.VERCEL_ENV?.trim() ?? "";
  return env === "production" || env === "preview";
}

export function authMode(): AuthMode {
  if (vercelDeployed()) return "supabase";
  const raw = (process.env.KAIROS_AUTH_MODE ?? "").trim().toLowerCase();
  if (raw === "supabase") return "supabase";
  if (raw === "local_shared" || raw === "") return "local_shared";
  // Unrecognised value: fail closed. Guessing `local_shared` would reopen
  // the unauthenticated-proxy hole on a host that tried to set a mode.
  return "supabase";
}

export function isSupabaseAuth(): boolean {
  return authMode() === "supabase";
}
