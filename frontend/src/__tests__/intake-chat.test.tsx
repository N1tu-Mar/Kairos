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
      memory: {
        revision: 0,
        provisional_summary: "",
        confirmed_summary: "",
        claims: {},
        updated_at: "2026-09-01T00:00:00Z",
      },
      pending_confirmation_batch: null,
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

    render(<IntakeChat profile={null} />);

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
    render(<IntakeChat profile={null} />);

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
    render(<IntakeChat profile={null} />);

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
    render(<IntakeChat profile={null} />);

    expect(await screen.findByText(/onerror/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("confirms exactly the displayed proposal batch and announces the memory update", async () => {
    const user = userEvent.setup();
    const initial = intakeView();
    const proposed = intakeView({
      session: {
        ...initial.session,
        revision: 4,
        fields: {
          startup_description: {
            field: "startup_description",
            status: "proposed",
            value: "A scheduling platform for shared laboratories.",
            confidence: 0.98,
            evidence: [],
            proposed_at: "2026-09-01T00:01:00Z",
            confirmed_at: null,
            confirmed_by: null,
          },
        },
        pending_confirmation_batch: {
          batch_id: "batch_4",
          source_message_id: "message_4",
          field_names: ["startup_description"],
          claim_ids: [],
          created_at: "2026-09-01T00:01:01Z",
        },
      },
    });
    const confirmed = intakeView({
      ...proposed,
      session: {
        ...proposed.session,
        revision: 5,
        pending_confirmation_batch: null,
        fields: {
          startup_description: {
            ...proposed.session.fields.startup_description!,
            status: "confirmed",
            confirmed_at: "2026-09-01T00:02:00Z",
            confirmed_by: "founder-user",
          },
        },
      },
    });
    const fetchMock = vi.fn(
      async (...args: [input: RequestInfo | URL, init?: RequestInit]) => {
        void args;
        return fetchMock.mock.calls.length === 1
          ? jsonResponse(proposed)
          : jsonResponse(confirmed);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<IntakeChat profile={null} />);

    await user.click(await screen.findByRole("button", { name: /confirm all captured facts/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(/all displayed proposals/i);
    const [url, init] = fetchMock.mock.calls[1]!;
    expect(url).toBe("/api/intake/intake_123/proposal-batches/batch_4/confirm");
    expect(JSON.parse(String(init?.body))).toEqual({ expected_revision: 4 });
  });

  it("rejects an invalid upload in the browser before making a request", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const fetchMock = vi.fn(async () => jsonResponse(intakeView()));
    vi.stubGlobal("fetch", fetchMock);
    render(<IntakeChat profile={null} />);

    const picker = await screen.findByLabelText(/drop a brief or pitch deck/i);
    await user.upload(
      picker,
      new File(["binary"], "malware.exe", { type: "application/octet-stream" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(/pdf, pptx, txt, or markdown/i);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("uploads and removes a supported document through the authenticated proxies", async () => {
    const user = userEvent.setup();
    const document = {
      document_id: "document_1",
      session_id: "intake_123",
      founder_id: "founder_demo",
      filename: "brief.txt",
      media_type: "text/plain",
      byte_size: 18,
      slot: 1,
      status: "ready" as const,
      chunks: [],
      error: null,
      created_at: "2026-09-01T00:03:00Z",
    };
    let uploaded = false;
    const fetchMock = vi.fn(
      async (...args: [input: RequestInfo | URL, init?: RequestInit]) => {
        const [input, init] = args;
        const url = String(input);
        if (url.endsWith("/documents") && init?.method === "POST") {
          uploaded = true;
          return jsonResponse(document, 201);
        }
        if (url.endsWith("/documents/document_1") && init?.method === "DELETE") {
          uploaded = false;
          return jsonResponse({ removed: true });
        }
        return jsonResponse(intakeView({ documents: uploaded ? [document] : [] }));
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<IntakeChat profile={null} />);

    const picker = await screen.findByLabelText(/drop a brief or pitch deck/i);
    await user.upload(picker, new File(["A bounded brief."], "brief.txt", { type: "text/plain" }));
    expect(await screen.findByText("brief.txt")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/extracted and added as evidence/i);

    const uploadCall = fetchMock.mock.calls.find(
      ([input, init]) => String(input).endsWith("/documents") && init?.method === "POST",
    );
    expect(uploadCall?.[1]?.body).toBeInstanceOf(FormData);

    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByText("brief.txt")).toBeNull());
    expect(screen.getByRole("status")).toHaveTextContent(/brief.txt was removed/i);
  });

  it("confirms one proposed fact with the current optimistic revision", async () => {
    const user = userEvent.setup();
    const initial = intakeView();
    const proposedFact = {
      field: "startup_description" as const,
      status: "proposed" as const,
      value: "A platform for university laboratories.",
      confidence: 0.94,
      evidence: [],
      proposed_at: "2026-09-01T00:01:00Z",
      confirmed_at: null,
      confirmed_by: null,
    };
    const proposed = intakeView({
      session: {
        ...initial.session,
        revision: 7,
        fields: { startup_description: proposedFact },
      },
    });
    const confirmed = intakeView({
      session: {
        ...proposed.session,
        revision: 8,
        fields: {
          startup_description: {
            ...proposedFact,
            status: "confirmed",
            confirmed_at: "2026-09-01T00:02:00Z",
            confirmed_by: "founder-user",
          },
        },
      },
    });
    const fetchMock = vi.fn(
      async (...args: [input: RequestInfo | URL, init?: RequestInit]) => {
        void args;
        return fetchMock.mock.calls.length === 1
          ? jsonResponse(proposed)
          : jsonResponse(confirmed);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<IntakeChat profile={null} />);

    await user.click(await screen.findByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/confirmed founder memory was updated/i);
    const [url, init] = fetchMock.mock.calls[1]!;
    expect(url).toBe("/api/intake/intake_123/fields/startup_description");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ action: "confirm", expected_revision: 7 });
  });

  it("enables completion only when the backend declares the session ready", async () => {
    const user = userEvent.setup();
    const ready = intakeView({ missing_required: [], ready_to_complete: true });
    const fetchMock = vi.fn(
      async (...args: [input: RequestInfo | URL, init?: RequestInit]) => {
        void args;
        return fetchMock.mock.calls.length === 1
          ? jsonResponse(ready)
          : jsonResponse(founderProfile());
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<IntakeChat profile={null} />);

    await user.click(await screen.findByRole("button", { name: /finish founder profile/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(/ready for matching and drafting/i);
    expect(screen.getByRole("button", { name: /founder memory completed/i })).toBeDisabled();
    const [url, init] = fetchMock.mock.calls[1]!;
    expect(url).toBe("/api/intake/intake_123/complete");
    expect(JSON.parse(String(init?.body))).toEqual({ expected_revision: 0 });
  });

  it("loads provenance and renders it as plain text", async () => {
    const user = userEvent.setup();
    const initial = intakeView({
      session: {
        ...intakeView().session,
        fields: {
          startup_description: {
            field: "startup_description",
            status: "proposed",
            value: "A lab platform.",
            confidence: 0.9,
            evidence: [{
              source_type: "document",
              source_id: "document_1:chunk:1",
              location: "slide:2",
              excerpt: null,
            }],
            proposed_at: "2026-09-01T00:01:00Z",
            confirmed_at: null,
            confirmed_by: null,
          },
        },
      },
    });
    const fetchMock = vi.fn(
      async (...args: [input: RequestInfo | URL, init?: RequestInit]) => {
        void args;
        return fetchMock.mock.calls.length === 1
          ? jsonResponse(initial)
          : jsonResponse({
              source_type: "document",
              source_id: "document_1:chunk:1",
              location: "slide:2",
              excerpt: "<img src=x onerror=alert(1)> 47 pilot labs",
            });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<IntakeChat profile={null} />);

    await user.click(await screen.findByRole("button", { name: /evidence.*slide 2/i }));
    expect(await screen.findByLabelText("Supporting evidence")).toHaveTextContent("47 pilot labs");
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("IntakeSection", () => {
  it("opens immediately for a founder without a profile", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(intakeView())),
    );
    render(<IntakeSection profile={null} />);
    expect(await screen.findByRole("textbox")).toBeInTheDocument();
  });

  it("stays collapsed for an existing profile until requested", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(intakeView())),
    );
    render(<IntakeSection profile={founderProfile()} />);
    expect(screen.queryByRole("textbox")).toBeNull();

    await user.click(
      screen.getByRole("button", { name: /continue founder interview/i }),
    );
    expect(await screen.findByRole("textbox")).toBeInTheDocument();
  });
});
