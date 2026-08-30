// @vitest-environment node

import { describe, expect, it } from "vitest";

interface HeaderGroup {
  headers: Array<{ key: string; value: string }>;
}

/**
 * Browsers enforce multiple CSP headers by intersecting them. Even a correct
 * nonce policy from middleware would break again if next.config.mjs added a
 * second `script-src 'self'`: Next's streamed replacement scripts would pass
 * the nonce policy and fail the static one, leaving loading.tsx on screen.
 */
describe("the static Next.js headers", () => {
  it("leave Content-Security-Policy to the per-request nonce middleware", async () => {
    const configUrl = new URL("../../next.config.mjs", import.meta.url);
    const { default: nextConfig } = await import(configUrl.href);
    const groups = (await nextConfig.headers()) as HeaderGroup[];
    const headerNames = groups.flatMap((group) =>
      group.headers.map((header) => header.key.toLowerCase()),
    );

    expect(headerNames).not.toContain("content-security-policy");
  });
});
