import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/login-form";

/**
 * The three ways into Kairos.
 *
 * Email and password was the only one, which meant every account had to be
 * created by hand in the Supabase dashboard. Google and GitHub make sign-in
 * self-serve; `api/provisioning.py` is what gives the resulting person a
 * founder to look at.
 *
 * What is worth testing here is not that a button calls a library. It is that
 * the provider is sent back to *this* origin's callback, that `next` survives
 * the round trip without becoming an open redirect, and that a failure says
 * the same thing regardless of which half of the credential was wrong.
 */

const replace = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));

const signInWithPassword = vi.fn();
const signInWithOAuth = vi.fn();
vi.mock("@/lib/supabase/browser", () => ({
  browserSupabase: () => ({
    auth: { signInWithPassword, signInWithOAuth },
  }),
}));

beforeEach(() => {
  replace.mockClear();
  refresh.mockClear();
  signInWithPassword.mockReset().mockResolvedValue({ error: null });
  signInWithOAuth.mockReset().mockResolvedValue({ error: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the provider buttons", () => {
  it.each([
    ["Google", "google"],
    ["GitHub", "github"],
  ])("starts a %s sign-in", async (label, provider) => {
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.click(screen.getByRole("button", { name: new RegExp(label, "i") }));

    await waitFor(() =>
      expect(signInWithOAuth).toHaveBeenCalledWith(
        expect.objectContaining({ provider }),
      ),
    );
  });

  it("sends the provider back to this origin's callback", async () => {
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.click(screen.getByRole("button", { name: /google/i }));

    await waitFor(() => expect(signInWithOAuth).toHaveBeenCalled());
    const { options } = signInWithOAuth.mock.calls[0]![0];
    // Hardcoding a deployment URL here is how a preview deploy sends people
    // to production. `window.location.origin` is whichever host they are on.
    expect(options.redirectTo).toBe(
      `${window.location.origin}/auth/callback`,
    );
  });

  it("carries `next` through the provider round trip", async () => {
    const user = userEvent.setup();
    render(<LoginForm next="/inbox" />);

    await user.click(screen.getByRole("button", { name: /github/i }));

    await waitFor(() => expect(signInWithOAuth).toHaveBeenCalled());
    const { options } = signInWithOAuth.mock.calls[0]![0];
    expect(options.redirectTo).toBe(
      `${window.location.origin}/auth/callback?next=%2Finbox`,
    );
  });

  it("refuses to carry an off-site `next`", async () => {
    const user = userEvent.setup();
    render(<LoginForm next="https://evil.example/steal" />);

    await user.click(screen.getByRole("button", { name: /google/i }));

    await waitFor(() => expect(signInWithOAuth).toHaveBeenCalled());
    const { options } = signInWithOAuth.mock.calls[0]![0];
    // The callback guards this too. Guarding it in both places means neither
    // has to assume the other did.
    expect(options.redirectTo).toBe(`${window.location.origin}/auth/callback`);
  });

  it("reports a provider that refuses to start", async () => {
    signInWithOAuth.mockResolvedValue({ error: { message: "provider down" } });
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.click(screen.getByRole("button", { name: /google/i }));

    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});

describe("email and password", () => {
  it("still signs in", async () => {
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.type(screen.getByLabelText(/email/i), "founder@example.com");
    await user.type(screen.getByLabelText(/password/i), "correct-horse");
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));

    await waitFor(() =>
      expect(signInWithPassword).toHaveBeenCalledWith({
        email: "founder@example.com",
        password: "correct-horse",
      }),
    );
  });

  it("says the same thing for a wrong password as for an unknown address", async () => {
    signInWithPassword.mockResolvedValue({ error: { message: "Invalid login" } });
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.type(screen.getByLabelText(/email/i), "founder@example.com");
    await user.type(screen.getByLabelText(/password/i), "wrong");
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));

    // Distinguishing them turns the form into a way to ask whether a given
    // person has an account here.
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "That email and password do not match an account.",
    );
  });
});
