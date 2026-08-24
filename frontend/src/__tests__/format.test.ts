import { describe, expect, it } from "vitest";

import {
  formatDuration,
  isDemo,
  runHeadline,
  splitHeadline,
} from "@/lib/format";
import { runReport } from "./fixtures";

describe("splitHeadline", () => {
  it("keeps the backend-composed facts verbatim", () => {
    const { title, facts } = splitHeadline(
      "[DEMO] Campus Innovation Fund · up to $10,000 · 24 days left · ~5h of work",
    );
    expect(title).toBe("[DEMO] Campus Innovation Fund");
    expect(facts).toEqual(["up to $10,000", "24 days left", "~5h of work"]);
  });

  it("survives a headline with no separator", () => {
    expect(splitHeadline("Just a title")).toEqual({
      title: "Just a title",
      facts: [],
    });
  });
});

describe("isDemo", () => {
  it("detects the synthetic marker so it is never stripped", () => {
    expect(isDemo("[DEMO] Campus Innovation Fund")).toBe(true);
    expect(isDemo("Rutgers Innovation Fund")).toBe(false);
    expect(isDemo(null)).toBe(false);
  });
});

describe("runHeadline", () => {
  it("matches RunReport.headline() in Python", () => {
    expect(runHeadline(runReport())).toBe(
      "Scanned 214. Discarded 198. Judged 16. Surfaced 3.",
    );
  });
});

describe("formatDuration", () => {
  it("scales from milliseconds to minutes", () => {
    expect(formatDuration(0.25)).toBe("250 ms");
    expect(formatDuration(72.4)).toBe("1m 12s");
    expect(formatDuration(-1)).toBe("—");
  });
});
