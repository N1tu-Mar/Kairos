import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AccountBar } from "@/components/account-bar";

/**
 * Signing out.
 *
 * `/auth/signout` has existed since the gate was built and nothing linked to
 * it, so the only way out of a session was to clear cookies by hand. This is
 * the link, and the two properties worth holding are that it is a POST and
 * that it says which account it would end.
 */

describe("the account bar", () => {
  it("names the signed-in account", () => {
    render(<AccountBar email="founder@example.com" />);

    expect(screen.getByText("founder@example.com")).toBeTruthy();
  });

  it("signs out with a POST, never a link", () => {
    const { container } = render(<AccountBar email="founder@example.com" />);

    const form = container.querySelector("form");
    // A GET sign-out can be triggered by any page that makes the browser load
    // a URL — an image tag is enough — which hands a third-party site the
    // ability to log somebody out. `auth/signout/route.ts` only answers POST.
    expect(form?.getAttribute("method")?.toLowerCase()).toBe("post");
    expect(form?.getAttribute("action")).toBe("/auth/signout");
    expect(screen.getByRole("button", { name: /sign out/i })).toBeTruthy();
  });

  it("renders nothing when there is no session to end", () => {
    const { container } = render(<AccountBar email={null} />);

    // Local single-founder mode has no sign-in, so a sign-out control there
    // is a button that cannot do anything.
    expect(container.firstChild).toBeNull();
  });

  it("still offers sign-out when the provider gave no email", () => {
    render(<AccountBar email="" />);

    // An empty string is a signed-in session whose identity carries no
    // address. That is a missing label, not a missing session.
    expect(screen.getByRole("button", { name: /sign out/i })).toBeTruthy();
  });
});
