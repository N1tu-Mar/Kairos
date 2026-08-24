import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RejectionTable, SkipList } from "@/components/transparency";

describe("RejectionTable", () => {
  const rejections = [
    {
      opportunity_id: "drop_1",
      opportunity_title: "[DEMO] Doctoral Commercialization Award",
      check: "DEGREE_LEVEL",
      detail: "open to phd, postdoc only",
      founder_value: "undergrad",
      required_value: "phd/postdoc",
    },
    {
      opportunity_id: "drop_2",
      opportunity_title: "[DEMO] Campus Accelerator Cohort",
      check: "EQUITY",
      detail: "this funder takes equity",
      founder_value: "non-dilutive only",
      required_value: "equity accepted",
    },
  ];

  it("shows the exact check that fired and both sides of the comparison", () => {
    render(<RejectionTable rejections={rejections} />);

    expect(screen.getByText("DEGREE_LEVEL")).toBeInTheDocument();
    expect(screen.getByText("open to phd, postdoc only")).toBeInTheDocument();
    expect(screen.getByText("undergrad")).toBeInTheDocument();
    expect(screen.getByText("phd/postdoc")).toBeInTheDocument();
  });

  it("keeps synthetic rows marked", () => {
    render(<RejectionTable rejections={rejections} />);

    expect(screen.getAllByText("Demo data")).toHaveLength(2);
  });

  it("says nothing was rejected rather than rendering an empty table", () => {
    render(<RejectionTable rejections={[]} />);

    expect(
      screen.getByText(/nothing was rejected deterministically/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("SkipList", () => {
  it("groups skips by the stage that made the call", () => {
    render(
      <SkipList
        skips={[
          {
            opportunity_id: "skip_1",
            opportunity_title: "[DEMO] Student Venture Prize",
            stage: "escalation_policy",
            reason: "needs ~12h, the founder's ceiling is 8h",
          },
          {
            opportunity_id: "skip_2",
            opportunity_title: "[DEMO] Unrelated Fellowship",
            stage: "assessor",
            reason: "not a fit for the stated programme focus",
          },
        ]}
      />,
    );

    expect(screen.getByText("Escalation policy")).toBeInTheDocument();
    expect(screen.getByText("Assessor")).toBeInTheDocument();
    expect(
      screen.getByText("needs ~12h, the founder's ceiling is 8h"),
    ).toBeInTheDocument();
  });
});
