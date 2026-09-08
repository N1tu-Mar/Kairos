import { describe, expect, it } from "vitest";

import { safeExternalHref } from "@/lib/safe-external-href";

describe("safeExternalHref", () => {
  it.each([
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "//evil.example/path",
    "https://user:password@example.com/grant",
    "https://example.com/bad\nheader",
    "https://example.com:invalid/grant",
    "https://example.com/unescaped space",
    "https://example.com\\@evil.example/grant",
    "not a URL",
    " https://example.com/grant",
  ])("rejects %s", (value) => {
    expect(safeExternalHref(value)).toBeNull();
  });

  it.each([
    "https://example.com/grant",
    "http://legacy.example.org/program#eligibility",
  ])("accepts %s", (value) => {
    expect(safeExternalHref(value)).toBe(value);
  });
});
