/** Return a browser-safe public web link, or null for legacy unsafe data. */
export function safeExternalHref(value: unknown): string | null {
  if (typeof value !== "string" || value.length === 0 || value !== value.trim()) {
    return null;
  }
  if (
    value.startsWith("//") ||
    value.includes("\\") ||
    /\s|[\u0000-\u001f\u007f-\u009f]/u.test(value)
  ) {
    return null;
  }

  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      return null;
    }
    if (!parsed.hostname || parsed.username || parsed.password) {
      return null;
    }
    return value;
  } catch {
    return null;
  }
}
