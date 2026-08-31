import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InboxFilter, parseInboxView } from "@/components/inbox-filter";

describe("InboxFilter", () => {
  it("recognises the Needs You view and defaults unknown values", () => {
    expect(parseInboxView("needs_you")).toBe("needs_you");
    expect(parseInboxView("mystery")).toBe("active");
  });

  it("renders a counted Needs You tab", () => {
    render(
      <InboxFilter
        view="needs_you"
        counts={{ active: 2, needs_you: 3, passive: 1, all: 3 }}
      />,
    );

    const tab = screen.getByRole("link", { name: /needs you 3/i });
    expect(tab).toHaveAttribute("href", "/inbox?view=needs_you");
    expect(tab).toHaveAttribute("aria-current", "page");
  });
});
