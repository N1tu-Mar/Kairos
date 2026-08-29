import { NextResponse } from "next/server";

import { supabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * Sign out, then back to the login page.
 *
 * POST rather than GET on purpose: a GET sign-out can be triggered by any
 * page that can make the browser load a URL — an image tag is enough — which
 * turns logging someone out into something a third-party site can do to them.
 * Low harm, but there is no reason to accept it.
 */
export async function POST(request: Request) {
  const supabase = await supabaseServerClient();
  await supabase.auth.signOut();
  return NextResponse.redirect(new URL("/login", request.url), {
    status: 303,
  });
}
