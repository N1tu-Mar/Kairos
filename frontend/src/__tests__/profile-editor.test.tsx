import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfileEditor } from "@/components/profile-editor";
import { founderProfile } from "./fixtures";
import type { FounderProfile } from "@/lib/types";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

describe("ProfileEditor", () => {
  beforeEach(() => {
    refresh.mockClear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the WHOLE profile, with traction and knowledge base untouched", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(founderProfile()), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const profile = founderProfile();

    render(<ProfileEditor profile={profile} />);
    await user.click(screen.getByRole("button", { name: /edit these facts/i }));

    const institution = screen.getByLabelText(/institution/i);
    await user.clear(institution);
    await user.type(institution, "Princeton University");
    await user.click(
      screen.getByRole("button", { name: /save the whole profile/i }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, options] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/profile");
    expect(options.method).toBe("PUT");
    const sent = JSON.parse(String(options.body)) as FounderProfile;
    // The edited field changed…
    expect(sent.institution).toBe("Princeton University");
    // …and everything else — including the evidence — went through unchanged.
    expect(sent.founder_id).toBe(profile.founder_id);
    expect(sent.traction).toEqual(profile.traction);
    expect(sent.knowledge_base).toEqual(profile.knowledge_base);
    expect(sent.funding_range).toEqual(profile.funding_range);
    expect(sent.reuse_eligibility_answers).toBe(false);
  });

  it("saves the eligibility answer reuse preference with the whole profile", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(founderProfile()), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ProfileEditor profile={founderProfile()} />);
    await user.click(screen.getByRole("button", { name: /edit these facts/i }));
    await user.click(
      screen.getByRole("checkbox", { name: /reuse answers across similar/i }),
    );
    await user.click(
      screen.getByRole("button", { name: /save the whole profile/i }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, options] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    const sent = JSON.parse(String(options.body)) as FounderProfile;
    expect(sent.reuse_eligibility_answers).toBe(true);
  });

  it("refuses a funding floor above the ceiling before anything is sent", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<ProfileEditor profile={founderProfile()} />);
    await user.click(screen.getByRole("button", { name: /edit these facts/i }));

    const floor = screen.getByLabelText(/funding floor/i);
    await user.clear(floor);
    await user.type(floor, "999999");
    await user.click(
      screen.getByRole("button", { name: /save the whole profile/i }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /floor cannot be above the ceiling/i,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports a backend rejection instead of closing the form", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ error: "founder_id does not match the founder this dashboard serves." }),
          { status: 400 },
        ),
      ),
    );

    render(<ProfileEditor profile={founderProfile()} />);
    await user.click(screen.getByRole("button", { name: /edit these facts/i }));
    await user.click(
      screen.getByRole("button", { name: /save the whole profile/i }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /does not match/i,
    );
    expect(refresh).not.toHaveBeenCalled();
    // Still editing — the form did not swallow the failure.
    expect(
      screen.getByRole("button", { name: /save the whole profile/i }),
    ).toBeInTheDocument();
  });

  it("says plainly that saving replaces the whole profile", async () => {
    const user = userEvent.setup();
    render(<ProfileEditor profile={founderProfile()} />);
    await user.click(screen.getByRole("button", { name: /edit these facts/i }));

    expect(
      screen.getByText(/replaces the whole profile at once/i),
    ).toBeInTheDocument();
  });
});
