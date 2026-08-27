import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SchedulerFailures } from "@/components/scheduler-failures";
import type { SchedulerFailure } from "@/lib/types";

function failure(overrides: Partial<SchedulerFailure> = {}): SchedulerFailure {
  return {
    founder_id: "founder_demo",
    at: "2026-08-26T07:00:00Z",
    source: "scheduled",
    retry_count: 0,
    failure_class: "timeout",
    detail: "run exceeded the 1800s timeout and was cancelled",
    ...overrides,
  };
}

describe("SchedulerFailures", () => {
  it("renders nothing when there are no failures", () => {
    const { container } = render(<SchedulerFailures failures={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("distinguishes a run that never happened from a quiet run", () => {
    render(<SchedulerFailures failures={[failure()]} />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      /a run did not complete/i,
    );
    expect(screen.getByText(/nothing was searched/i)).toBeInTheDocument();
  });

  it("says which kind of failure it was in plain words", () => {
    render(
      <SchedulerFailures
        failures={[
          failure({ failure_class: "orphaned" }),
          failure({ failure_class: "startup" }),
        ]}
      />,
    );

    expect(screen.getByText(/backend restarted/i)).toBeInTheDocument();
    expect(screen.getByText(/could not be started/i)).toBeInTheDocument();
  });

  it("marks a scheduled invocation as scheduled and shows the retry count", () => {
    render(
      <SchedulerFailures
        failures={[failure({ source: "scheduled", retry_count: 1 })]}
      />,
    );

    expect(screen.getByText(/scheduled/)).toBeInTheDocument();
    expect(screen.getByText(/retry 1/)).toBeInTheDocument();
  });

  it("renders an unknown failure class without inventing a cause", () => {
    render(
      <SchedulerFailures
        failures={[failure({ failure_class: "something_new" })]}
      />,
    );

    expect(screen.getByText(/the run did not complete/i)).toBeInTheDocument();
  });

  it("shows the sanitised detail verbatim and adds nothing to it", () => {
    render(
      <SchedulerFailures
        failures={[failure({ detail: "401 from Bearer [REDACTED]" })]}
      />,
    );

    expect(screen.getByText("401 from Bearer [REDACTED]")).toBeInTheDocument();
  });
});
