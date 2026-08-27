import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ManualRunControl } from "@/components/manual-run";
import { runJob, runReport } from "./fixtures";
import type { RunJob, RunReport } from "@/lib/types";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/**
 * The backend accepts a run and answers with a job; the component polls until
 * the job is terminal. This fake plays both halves: one POST answer, then a
 * scripted sequence of poll answers.
 */
function fakeBackend(options: {
  accept?: Response;
  polls?: Array<{ job: RunJob; report: RunReport | null }>;
}) {
  const polls = [...(options.polls ?? [])];
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST" && url === "/api/runs") {
      return options.accept ?? json(runJob(), 202);
    }
    const next = polls.length > 1 ? polls.shift()! : polls[0];
    if (!next) throw new Error(`unexpected poll for ${url}`);
    return json(next);
  });
}

describe("ManualRunControl", () => {
  beforeEach(() => {
    refresh.mockClear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("presents itself as a manual action and never as a schedule", () => {
    render(<ManualRunControl />);

    expect(screen.getByText(/starts one run by hand/i)).toBeInTheDocument();
    expect(screen.getByText(/does not create a schedule/i)).toBeInTheDocument();
  });

  it("blocks repeated clicks while the request is in flight", async () => {
    const user = userEvent.setup();
    let resolvePost: (value: Response) => void = () => {};
    const fetchMock = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          return new Promise<Response>((resolve) => (resolvePost = resolve));
        }
        return Promise.resolve(
          json({
            job: runJob({ status: "succeeded", run_id: "run_1" }),
            report: runReport(),
          }),
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ManualRunControl />);
    const button = screen.getByRole("button", { name: /run kairos now/i });

    await user.click(button);
    await user.click(button);
    const posts = fetchMock.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === "POST",
    );
    expect(posts).toHaveLength(1);

    resolvePost(json(runJob({ status: "running" }), 202));

    await waitFor(() =>
      expect(screen.getByText(/finished in/i)).toBeInTheDocument(),
    );
  });

  it("sends an idempotency key so a retry cannot start a second run", async () => {
    const user = userEvent.setup();
    const fetchMock = fakeBackend({
      polls: [{ job: runJob({ status: "running" }), report: null }],
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ManualRunControl />);
    await user.click(screen.getByRole("button", { name: /run kairos now/i }));

    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse(String((init as RequestInit).body)) as {
      idempotency_key?: string;
    };
    expect(body.idempotency_key).toBeTruthy();
  });

  it("shows elapsed time rather than a fabricated progress bar", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      fakeBackend({
        polls: [{ job: runJob({ status: "running" }), report: null }],
      }),
    );

    render(<ManualRunControl />);
    await user.click(screen.getByRole("button", { name: /run kairos now/i }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        /does not report progress mid-run/i,
      ),
    );
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("reports a refused start honestly instead of pretending it ran", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json(
          {
            error: "Could not reach the Kairos API.",
            detail: "ECONNREFUSED",
            kind: "unreachable",
          },
          502,
        ),
      ),
    );

    render(<ManualRunControl />);
    await user.click(screen.getByRole("button", { name: /run kairos now/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/the run did not start/i);
    expect(alert).toHaveTextContent(/could not reach the kairos api/i);
    expect(alert).toHaveTextContent(/ECONNREFUSED/);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("treats an already-running run as an answer, not a failure", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json({ error: "A run is already in progress for this founder." }, 409),
      ),
    );

    render(<ManualRunControl />);
    await user.click(screen.getByRole("button", { name: /run kairos now/i }));

    expect(await screen.findByText(/already running/i)).toBeInTheDocument();
    // A conflict is not an error state — no alert, and the button comes back.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      screen.getByRole("button", { name: /run kairos now/i }),
    ).not.toBeDisabled();
  });

  it("polls until the job finishes and then shows the report", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      fakeBackend({
        polls: [
          { job: runJob({ status: "running" }), report: null },
          {
            job: runJob({ status: "succeeded", run_id: "run_1" }),
            report: runReport(),
          },
        ],
      }),
    );

    render(<ManualRunControl />);
    await user.click(screen.getByRole("button", { name: /run kairos now/i }));

    expect(await screen.findByText(/finished in/i, {}, { timeout: 5000 })).toBeInTheDocument();
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it("shows a failed job's reason rather than a report it does not have", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      fakeBackend({
        polls: [
          {
            job: runJob({
              status: "failed",
              error: "run exceeded the 1800s timeout and was cancelled",
            }),
            report: null,
          },
        ],
      }),
    );

    render(<ManualRunControl />);
    await user.click(screen.getByRole("button", { name: /run kairos now/i }));

    const alert = await screen.findByRole("alert", {}, { timeout: 5000 });
    expect(alert).toHaveTextContent(/the run failed/i);
    expect(alert).toHaveTextContent(/1800s timeout/);
  });

  it("reports a surfaced-nothing run as a legitimate result", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      fakeBackend({
        polls: [
          {
            job: runJob({ status: "succeeded", run_id: "run_1" }),
            report: runReport({ surfaced: 0 }),
          },
        ],
      }),
    );

    render(<ManualRunControl />);
    await user.click(screen.getByRole("button", { name: /run kairos now/i }));

    expect(
      await screen.findByText(/that is a legitimate result/i, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });
});
