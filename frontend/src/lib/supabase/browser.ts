import { createBrowserClient } from "@supabase/ssr";

import { supabaseAnonKey, supabaseUrl } from "@/lib/supabase/config";

/**
 * The Supabase client for the browser.
 *
 * Used by exactly one component — the login form — because signing in is when
 * the session cookie gets established and that has to happen client-side.
 * Nothing else in this app talks to Supabase from the browser: reads go
 * through Server Components, and writes go through the proxy routes.
 *
 * The anon key is public by design and carries no privilege on its own. The
 * service-role key is never used in this app, from either side.
 */
export function browserSupabase() {
  return createBrowserClient(supabaseUrl(), supabaseAnonKey());
}
