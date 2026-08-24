import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InboxItemCard } from "@/components/inbox-item-card";
import { inboxItem } from "./fixtures";

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
});
