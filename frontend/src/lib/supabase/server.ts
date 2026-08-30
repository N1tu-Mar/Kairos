import "server-only";

import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

import { supabaseAnonKey, supabaseUrl } from "@/lib/supabase/config";

/**
 * A Supabase client bound to this request's cookies.
 *
 * Server-side only. The session lives in httpOnly cookies that Supabase's SSR
 * helper manages, which is what keeps the access token out of reach of any
 * script running on the page — the reason this is not the browser client with
 * localStorage.
 */
export async function supabaseServerClient() {
  const store = await cookies();
  return createServerClient(supabaseUrl(), supabaseAnonKey(), {
    cookies: {
      getAll() {
        return store.getAll();
      },
      setAll(toSet) {
        try {
          for (const { name, value, options } of toSet) {
            store.set(name, value, options);
          }
        } catch {
          // Called from a Server Component, where cookies are read-only.
          // Harmless: `middleware.ts` refreshes the session on every request,
          // so the write that matters has already happened there.
        }
      },
    },
  });
}

/**
 * The signed-in user, verified against Supabase — or null.
 *
 * `getUser()` and not `getSession()`. `getSession()` reads the cookie and
 * trusts it; `getUser()` revalidates the token with the auth server, so a
 * forged or stale cookie does not produce a user here. The difference is the
 * whole security value of this function.
 */
export async function currentUser() {
  if (!supabaseUrl() || !supabaseAnonKey()) return null;
  const supabase = await supabaseServerClient();
  const { data, error } = await supabase.auth.getUser();
  return error ? null : data.user;
}

/**
 * The access token to present to FastAPI for this request, or "".
 *
 * This is the whole point of the login work: the credential the backend sees
 * is now *the user's*, not one shared token the proxy holds on everyone's
 * behalf. FastAPI verifies its signature and reads the founder memberships
 * for that subject, so a compromised dashboard cannot act as a founder it has
 * no session for.
 */
export async function currentAccessToken(): Promise<string> {
  if (!supabaseUrl() || !supabaseAnonKey()) return "";
  const supabase = await supabaseServerClient();
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? "";
}
