import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ManualRunControl } from "@/components/manual-run";
import { runReport } from "./fixtures";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

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
    expect(screen.getByText(/does not schedule anything/i)).toBeInTheDocument();
  });

  it("blocks repeated clicks while the request is in flight", async () => {
    const user = userEvent.setup();
    let resolveFetch: (value: Response) => void = () => {};
    const fetchMock = vi.fn(
      () => new Promise<Response>((resolve) => (resolveFetch = resolve)),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ManualRunControl />);
    const button = screen.getByRole("button", { name: /run kairos now/i });

    await user.click(button);
    expect(button).toBeDisabled();

    await user.click(button);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolveFetch(
      new Response(JSON.stringify(runReport()), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await waitFor(() =>
      expect(screen.getByText(/finished in/i)).toBeInTheDocument(),
    );
  });

  it("shows elapsed time rather than a fabricated progress bar", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})),
    );

    render(<ManualRunControl />);
    await user.click(screen.getByRole("button", { name: /run kairos now/i }));

    expect(screen.getByRole("status")).toHaveTextContent(
      /does not report progress mid-run/i,
    );
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("reports a backend failure honestly instead of pretending it ran", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: "Could not reach the Kairos API.",
            detail: "ECONNREFUSED",
            kind: "unreachable",
          }),
          { status: 502, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    render(<ManualRunControl />);
    await user.click(screen.getByRole("button", { name: /run kairos now/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/the run did not complete/i);
    expect(alert).toHaveTextContent(/could not reach the kairos api/i);
    expect(alert).toHaveTextContent(/ECONNREFUSED/);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("reports a surfaced-nothing run as a legitimate result", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify(runReport({ surfaced: 0 })), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    render(<ManualRunControl />);
    await user.click(screen.getByRole("button", { name: /run kairos now/i }));

    expect(
      await screen.findByText(/that is a legitimate result/i),
    ).toBeInTheDocument();
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });
});
