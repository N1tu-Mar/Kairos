import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { InboxItemCard } from "@/components/inbox-item-card";
import { inboxItem, opportunity } from "./fixtures";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

describe("InboxItemCard", () => {
  it("keeps the [DEMO] marker and flags synthetic records", () => {
    render(<InboxItemCard item={inboxItem()} />);

    expect(screen.getByText("[DEMO] Campus Innovation Fund")).toBeInTheDocument();
    expect(screen.getByText("Demo data")).toBeInTheDocument();
  });

  it("shows the deadline and effort the backend computed", () => {
    render(<InboxItemCard item={inboxItem()} />);

    expect(screen.getByText("24 days left")).toBeInTheDocument();
    expect(screen.getByText("up to $10,000")).toBeInTheDocument();
    expect(screen.getByText("5.0 h")).toBeInTheDocument();
  });

  it("links to the draft when one exists", () => {
    render(<InboxItemCard item={inboxItem()} />);

    expect(screen.getByRole("link", { name: /review the draft/i })).toHaveAttribute(
      "href",
      "/drafts/draft_1",
    );
  });

  it("says so plainly when there is no draft", () => {
    render(<InboxItemCard item={inboxItem({ draft_id: null })} />);

    expect(screen.queryByRole("link", { name: /review the draft/i })).toBeNull();
    expect(screen.getByText(/nothing was drafted/i)).toBeInTheDocument();
  });

  it("marks a passive item as also-found rather than a recommendation", () => {
    render(
      <InboxItemCard item={inboxItem({ passive: true, kind: "MAYBE" })} />,
    );

    expect(screen.getByText("Also found")).toBeInTheDocument();
    expect(screen.getByText("Maybe")).toBeInTheDocument();
  });

  it("surfaces a blocker the founder could actually remove", () => {
    const item = inboxItem();
    render(
      <InboxItemCard
        item={{
          ...item,
          assessment: {
            ...item.assessment!,
            blocker: "requires a faculty PI",
            blocker_founder_resolvable: true,
          },
        }}
      />,
    );

    expect(screen.getByText(/you can move it/i)).toBeInTheDocument();
    expect(screen.getByText("requires a faculty PI")).toBeInTheDocument();
  });

  it("renders structured facts and the funder's page link when the row resolves", () => {
    render(<InboxItemCard item={inboxItem()} opportunity={opportunity()} />);

    // The date comes from the structured deadline field, not the headline.
    expect(screen.getByText(/due oct 15, 2999/i)).toBeInTheDocument();
    expect(screen.getByText("$2,500 – $10,000")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /open the funder/i });
    expect(link).toHaveAttribute(
      "href",
      "https://example.invalid/campus-innovation-fund",
    );
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("falls back to the composed headline when the row cannot be resolved", () => {
    render(<InboxItemCard item={inboxItem()} opportunity={null} />);

    // The pre-rendered facts from the Python headline, verbatim.
    expect(screen.getByText("24 days left")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /open the funder/i })).toBeNull();
  });

  it("marks an unverified source instead of hiding it", () => {
    render(
      <InboxItemCard
        item={inboxItem()}
        opportunity={opportunity({ verified: false })}
      />,
    );

    expect(screen.getByText("Unverified source")).toBeInTheDocument();
  });

  it("flags a deadline that has already passed", () => {
    render(
      <InboxItemCard
        item={inboxItem()}
        opportunity={opportunity({ deadline: "2020-01-01" })}
      />,
    );

    expect(screen.getByText(/deadline passed/i)).toBeInTheDocument();
  });

  it("shows what the founder did with the item", () => {
    render(<InboxItemCard item={inboxItem({ state: "applied" })} />);

    expect(screen.getByText("Applied")).toBeInTheDocument();
  });
});
