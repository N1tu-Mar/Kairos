import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IntakeChat, IntakeSection } from "@/components/intake-chat";
import type { IntakeSessionView } from "@/lib/types";
import { founderProfile } from "./fixtures";

function intakeView(
  overrides: Partial<IntakeSessionView> = {},
): IntakeSessionView {
  return {
    session: {
      session_id: "intake_123",
      founder_id: "founder_demo",
      status: "active",
      revision: 0,
      pending_message_id: null,
      fields: {},
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      completed_at: null,
    },
    messages: [],
    documents: [],
    missing_required: [
      "citizenship",
      "degree_level",
      "entity_type",
      "equity_ok",
      "funding_range",
      "has_faculty_advisor",
      "institution",
      "max_application_hours",
      "stage",
      "startup_description",
      "team_size",
    ],
    ready_to_complete: false,
    turn_pending: false,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("IntakeChat", () => {
  it("resumes a persistent session and replaces the scripted questionnaire", async () => {
    const fetchMock = vi.fn(
      async (...args: [input: RequestInfo | URL, init?: RequestInit]) => {
        void args;
        return jsonResponse(intakeView());
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<IntakeChat profile={null} founderId="founder_demo" />);

    expect(await screen.findByRole("textbox", { name: /message kairos/i })).toBeVisible();
    expect(screen.getByText(/tell me what you.re building/i)).toBeInTheDocument();
    expect(screen.queryByText(/what should i call you/i)).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith("/api/intake", { method: "POST" });
  });

  it("sends an idempotent turn and renders the persisted reply and proposal", async () => {
    const user = userEvent.setup();
    const initial = intakeView();
    const replied = intakeView({
      session: {
        ...initial.session,
        revision: 2,
        fields: {
          startup_description: {
            field: "startup_description",
            status: "proposed",
            value: "A scheduling platform for shared university labs.",
            confidence: 0.96,
            evidence: [],
            proposed_at: "2026-09-01T00:01:00Z",
            confirmed_at: null,
            confirmed_by: null,
          },
        },
      },
      messages: [
        {
          message_id: "message_founder",
          session_id: "intake_123",
          founder_id: "founder_demo",
          role: "founder",
          text: "We coordinate shared university lab equipment.",
          client_message_id: "web-one",
          in_reply_to: null,
          created_at: "2026-09-01T00:01:00Z",
        },
        {
          message_id: "message_agent",
          session_id: "intake_123",
          founder_id: "founder_demo",
          role: "assistant",
          text: "That gives me the product. What stage are you at today?",
          client_message_id: "reply:web-one",
          in_reply_to: "message_founder",
          created_at: "2026-09-01T00:01:01Z",
        },
      ],
      missing_required: initial.missing_required.filter(
        (field) => field !== "startup_description",
      ),
    });
    const fetchMock = vi.fn(
      async (...args: [input: RequestInfo | URL, init?: RequestInit]) => {
        void args;
        return fetchMock.mock.calls.length === 1
          ? jsonResponse(initial)
          : jsonResponse(replied);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<IntakeChat profile={null} founderId="founder_demo" />);

    const input = await screen.findByRole("textbox", { name: /message kairos/i });
    await user.type(input, "We coordinate shared university lab equipment.");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(
      await screen.findByText(/what stage are you at today/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/scheduling platform for shared university labs/i)).toBeInTheDocument();
    expect(screen.getByText(/needs your confirmation/i)).toBeInTheDocument();

    const [url, init] = fetchMock.mock.calls[1]!;
    expect(url).toBe("/api/intake/intake_123/messages");
    expect(init?.method).toBe("POST");
    const sent = JSON.parse(String(init?.body));
    expect(sent.text).toBe("We coordinate shared university lab equipment.");
    expect(sent.expected_revision).toBe(0);
    expect(sent.client_message_id).toMatch(/^web-/);
  });

  it("reuses the same idempotency key when a failed request is retried", async () => {
    const user = userEvent.setup();
    const view = intakeView();
    const fetchMock = vi.fn(
      async (...args: [input: RequestInfo | URL, init?: RequestInit]) => {
        void args;
        switch (fetchMock.mock.calls.length) {
          case 1:
          case 3:
            return jsonResponse(view);
          case 2:
            return jsonResponse({ error: "The assistant is temporarily unavailable." }, 503);
          default:
            return jsonResponse({
              ...view,
              session: { ...view.session, revision: 2 },
              messages: [],
            });
        }
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<IntakeChat profile={null} founderId="founder_demo" />);

    const input = await screen.findByRole("textbox", { name: /message kairos/i });
    await user.type(input, "My startup helps labs.");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/safe to retry/i);

    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));

    const firstBody = JSON.parse(String(fetchMock.mock.calls[1]![1]?.body));
    const retryBody = JSON.parse(String(fetchMock.mock.calls[3]![1]?.body));
    expect(retryBody.client_message_id).toBe(firstBody.client_message_id);
  });

  it("renders server text as plain content instead of executable HTML", async () => {
    const unsafe = intakeView({
      messages: [
        {
          message_id: "message_agent",
          session_id: "intake_123",
          founder_id: "founder_demo",
          role: "assistant",
          text: '<img src=x onerror="window.pwned=true">',
          client_message_id: null,
          in_reply_to: null,
          created_at: "2026-09-01T00:01:00Z",
        },
      ],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(unsafe)),
    );
    render(<IntakeChat profile={null} founderId="founder_demo" />);

    expect(await screen.findByText(/onerror/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("IntakeSection", () => {
  it("opens immediately for a founder without a profile", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(intakeView())),
    );
    render(<IntakeSection profile={null} founderId="founder_demo" />);
    expect(await screen.findByRole("textbox")).toBeInTheDocument();
  });

  it("stays collapsed for an existing profile until requested", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(intakeView())),
    );
    render(<IntakeSection profile={founderProfile()} founderId="founder_demo" />);
    expect(screen.queryByRole("textbox")).toBeNull();

    await user.click(
      screen.getByRole("button", { name: /continue founder interview/i }),
    );
    expect(await screen.findByRole("textbox")).toBeInTheDocument();
  });
});
