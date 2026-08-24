import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunSummary } from "@/components/run-summary";
import { runReport } from "./fixtures";

describe("RunSummary", () => {
  it("shows the four counters verbatim", () => {
    render(<RunSummary report={runReport()} />);

    expect(screen.getByText("Scanned")).toBeInTheDocument();
    expect(screen.getByText("198")).toBeInTheDocument();
    expect(screen.getByText("214")).toBeInTheDocument();
  });

  it("treats nothing surfaced as a result, not an error", () => {
    render(<RunSummary report={runReport({ surfaced: 0 })} />);

    expect(
      screen.getByText(/that is a result, not a failure/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not claim a quiet result when the run halted", () => {
    render(
      <RunSummary
        report={runReport({
          surfaced: 0,
          halted_reason: "MAX_RUN_TOKENS: ceiling reached after 12 assessments",
        })}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      /halted before it finished/i,
    );
    expect(screen.getByText(/MAX_RUN_TOKENS/)).toBeInTheDocument();
    expect(
      screen.queryByText(/that is a result, not a failure/i),
    ).not.toBeInTheDocument();
  });

  it("reports source failures instead of smoothing them over", () => {
    render(
      <RunSummary
        report={runReport({
          sources_failed: [
            {
              source: "grants_gov",
              detail: "read timeout after 15.0s",
              at: "2026-08-23T06:00:20Z",
            },
          ],
        })}
      />,
    );

    expect(screen.getByText(/one source did not answer/i)).toBeInTheDocument();
    expect(screen.getByText(/read timeout after 15.0s/)).toBeInTheDocument();
  });
});
