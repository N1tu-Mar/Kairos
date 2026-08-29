/**
 * Stand-in for the `server-only` package under vitest.
 *
 * The real module throws on import outside a React Server Component, which is
 * exactly what it is for — `src/lib/api.ts` imports it so the bearer token
 * cannot be pulled into a client bundle. That guard also stops a test from
 * importing the route handlers it needs to check, so the alias in
 * `vitest.config.ts` swaps it for this empty module.
 *
 * The production guard is untouched: only the vitest resolver sees this file.
 */
export {};
