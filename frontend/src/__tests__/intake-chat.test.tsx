import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IntakeChat, IntakeSection } from "@/components/intake-chat";
import { founderProfile } from "./fixtures";
import type { FounderProfile } from "@/lib/types";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

/**
 * Walk the whole script, answering every question.
 *
 * The order here mirrors `SCRIPT` in the component. It is written out rather
 * than derived so that reordering the script breaks this test loudly instead
 * of quietly agreeing with itself.
 */
const ANSWERS = [
  "Ada Lovelace", // full_name
  "Rutgers University", // institution
  "Computer Science", // major
  "undergrad", // degree_level
  "us_citizen", // citizenship
  "A study tool that turns lecture recordings into practice questions.", // building
  "prototype", // stage
  "none", // entity_type
  "2", // team_size
  "120", // traction.users
  "3", // traction.pitches
  "0", // traction.revenue_usd
  "5000", // funding_floor
  "50000", // funding_ceiling
  "no", // equity_ok
  "yes", // has_faculty_advisor
  "8", // max_application_hours
  "US-NJ, US", // geographies
];

async function answerEverything(user: ReturnType<typeof userEvent.setup>) {
  for (const answer of ANSWERS) {
    const input = screen.getByRole("textbox");
    await user.type(input, answer);
    await user.click(screen.getByRole("button", { name: "Send" }));
  }
}

/** A stubbed `fetch` that answers the profile PUT, typed so its calls read back. */
function makeFetchMock() {
  return vi.fn(
    async (...args: [url: string, init: RequestInit]) => {
      void args;
      return new Response(JSON.stringify(founderProfile()), { status: 200 });
    },
  );
}

/** The body of the single PUT the component makes. */
function sentProfile(fetchMock: ReturnType<typeof makeFetchMock>): FounderProfile {
  const init = fetchMock.mock.calls[0]?.[1];
  return JSON.parse(String(init?.body));
}

describe("IntakeChat", () => {
  beforeEach(() => {
    refresh.mockClear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("turns a conversation into a whole profile and PUTs it once", async () => {
    const user = userEvent.setup();
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<IntakeChat profile={null} founderId="founder_demo" />);
    await answerEverything(user);
    await user.click(screen.getByRole("button", { name: /save my profile/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const call = fetchMock.mock.calls[0];
    expect(call?.[0]).toBe("/api/profile");
    expect(call?.[1]?.method).toBe("PUT");

    const sent = sentProfile(fetchMock);
    expect(sent.full_name).toBe("Ada Lovelace");
    expect(sent.institution).toBe("Rutgers University");
    expect(sent.major).toBe("Computer Science");
    expect(sent.degree_level).toBe("undergrad");
    expect(sent.team_size).toBe(2);
    expect(sent.funding_range).toEqual([5_000, 50_000]);
    expect(sent.equity_ok).toBe(false);
    expect(sent.has_faculty_advisor).toBe(true);
    expect(sent.geographies).toEqual(["US-NJ", "US"]);
  });

  it("records the traction numbers a founder gives it", async () => {
    const user = userEvent.setup();
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<IntakeChat profile={null} founderId="founder_demo" />);
    await answerEverything(user);
    await user.click(screen.getByRole("button", { name: /save my profile/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(sentProfile(fetchMock).traction).toEqual({
      users: 120,
      pitches: 3,
      revenue_usd: 0,
    });
  });

  it("puts what the founder wrote about the work in the knowledge base, not in a field", async () => {
    const user = userEvent.setup();
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<IntakeChat profile={null} founderId="founder_demo" />);
    await answerEverything(user);
    await user.click(screen.getByRole("button", { name: /save my profile/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const sent = sentProfile(fetchMock);
    const chunk = sent.knowledge_base.at(-1);
    expect(chunk?.text).toContain("lecture recordings");
    expect(chunk?.source).toBe("onboarding_chat");
    // The prose must not have leaked into anything the filter reads.
    expect(sent.institution).toBe("Rutgers University");
    expect(sent.citizenship).toBe("us_citizen");
  });

  it("refuses an answer it cannot parse and asks again rather than storing it", async () => {
    const user = userEvent.setup();
    render(<IntakeChat profile={null} founderId="founder_demo" />);

    // Skip past the name to the institution, then give the degree step junk.
    for (const answer of ["Ada", "Rutgers", "skip"]) {
      await user.type(screen.getByRole("textbox"), answer);
      await user.click(screen.getByRole("button", { name: "Send" }));
    }
    await user.type(screen.getByRole("textbox"), "sophomore-ish");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(screen.getByText(/pick one of/i)).toBeInTheDocument();
    // Still on the same question, not advanced past it.
    expect(screen.getAllByText("What level?").length).toBeGreaterThan(0);
  });

  it("keeps an existing profile's knowledge base instead of replacing it", async () => {
    const user = userEvent.setup();
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    const existing = founderProfile({
      knowledge_base: [
        {
          chunk_id: "existing",
          text: "Told to Kairos last week.",
          source: "onboarding_q1",
          confidence: 1,
          created_at: "2026-08-01T00:00:00Z",
        },
      ],
    });

    render(<IntakeChat profile={existing} founderId="founder_demo" />);
    await answerEverything(user);
    await user.click(screen.getByRole("button", { name: /save my profile/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const sent = sentProfile(fetchMock);
    expect(sent.knowledge_base).toHaveLength(2);
    expect(sent.knowledge_base[0].chunk_id).toBe("existing");
  });

  it("says nothing was stored until the founder saves", async () => {
    const user = userEvent.setup();
    render(<IntakeChat profile={null} founderId="founder_demo" />);
    await answerEverything(user);
    expect(screen.getByText(/nothing is stored until you save/i)).toBeInTheDocument();
  });
});

describe("IntakeSection", () => {
  it("opens the conversation immediately when there is no profile", () => {
    render(<IntakeSection profile={null} founderId="founder_demo" />);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("stays collapsed when a profile already exists", async () => {
    const user = userEvent.setup();
    render(<IntakeSection profile={founderProfile()} founderId="founder_demo" />);
    expect(screen.queryByRole("textbox")).toBeNull();

    await user.click(screen.getByRole("button", { name: /tell kairos more/i }));
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("points out a profile carrying no traction numbers", () => {
    render(<IntakeSection profile={founderProfile({ traction: {} })} founderId="founder_demo" />);
    expect(screen.getByText(/no traction numbers/i)).toBeInTheDocument();
  });
});
