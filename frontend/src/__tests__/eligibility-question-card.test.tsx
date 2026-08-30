import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EligibilityQuestionCard } from "@/components/eligibility-question-card";
import { eligibilityQuestion } from "./fixtures";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  refresh.mockClear();
});

describe("EligibilityQuestionCard", () => {
  it("saves a definite founder answer and refreshes the queue", async () => {
    const user = userEvent.setup();
    const updated = eligibilityQuestion({ status: "answered", answer: "yes" });
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(updated), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<EligibilityQuestionCard question={eligibilityQuestion()} />);
    await user.click(screen.getByRole("button", { name: "Yes" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, options] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/eligibility-questions/eq_demo_1/answer");
    expect(JSON.parse(String(options.body))).toEqual({
      answer: "yes",
    });
    expect(await screen.findByText("Answered")).toBeInTheDocument();
    expect(refresh).toHaveBeenCalled();
  });

  it("keeps not-sure selected without pretending the question is resolved", async () => {
    const user = userEvent.setup();
    const updated = eligibilityQuestion({ answer: "not_sure" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(updated), { status: 200 })),
    );

    render(<EligibilityQuestionCard question={eligibilityQuestion()} />);
    await user.click(screen.getByRole("button", { name: "Not sure" }));

    expect(await screen.findByText("Needs you")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not sure" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("keeps the question visible when saving fails", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ error: "Could not save this answer." }), {
          status: 500,
        }),
      ),
    );

    render(<EligibilityQuestionCard question={eligibilityQuestion()} />);
    await user.click(screen.getByRole("button", { name: "No" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not save/i);
    expect(screen.getByText(/51% of the company/i)).toBeInTheDocument();
  });
});
