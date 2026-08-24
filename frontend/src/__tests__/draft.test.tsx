import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  DraftCountsSummary,
  DraftFieldCard,
  GateOutcome,
} from "@/components/draft";
import { counts, draft } from "./fixtures";

describe("DraftCountsSummary", () => {
  it("leads with how many questions still need the founder", () => {
    render(<DraftCountsSummary counts={counts()} />);

    expect(
      screen.getByText("7 questions on this form. 1 needs you."),
    ).toBeInTheDocument();
  });

  it("says none rather than zero when nothing is outstanding", () => {
    render(<DraftCountsSummary counts={counts({ NEEDS_FOUNDER: 0 })} />);

    expect(screen.getByText(/none of them need you/i)).toBeInTheDocument();
  });
});

describe("GateOutcome", () => {
  it("names the failed check when the draft is blocked", () => {
    render(
      <GateOutcome
        gate={{
          passed: false,
          checks_run: ["BLOCKLIST", "PROVENANCE"],
          failed_check: "PROVENANCE",
          violations: [
            {
              check: "PROVENANCE",
              field_id: "evidence",
              detail: "GENERATED field carries no source span",
              severity: "BLOCK",
            },
          ],
        }}
      />,
    );

    expect(screen.getAllByText("PROVENANCE").length).toBeGreaterThan(0);
    expect(
      screen.getByText("GENERATED field carries no source span"),
    ).toBeInTheDocument();
  });

  it("does not read a missing gate result as a pass", () => {
    render(<GateOutcome gate={null} />);

    expect(screen.getByText(/not cleared for review/i)).toBeInTheDocument();
  });
});

describe("DraftFieldCard", () => {
  it("shows the quote behind a generated answer", () => {
    const generated = draft().fields[0];
    render(<DraftFieldCard field={generated} />);

    expect(screen.getByText("Generated")).toBeInTheDocument();
    expect(screen.getByText("Audit: supported")).toBeInTheDocument();
    expect(screen.getByText(/where this came from/i)).toBeInTheDocument();
    expect(screen.getByText("pitch_deck.pdf p.1")).toBeInTheDocument();
  });

  it("explains a NEEDS_FOUNDER field instead of leaving it blank", () => {
    const needsFounder = draft().fields[1];
    const { container } = render(<DraftFieldCard field={needsFounder} />);

    expect(screen.getByText("Needs you")).toBeInTheDocument();
    expect(screen.getByText(/would mean inventing something/i)).toBeInTheDocument();
    // Strong visual distinction, not just a label.
    expect(container.firstElementChild?.className).toContain("border-l-alert");
  });
});
