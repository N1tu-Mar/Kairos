import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InboxStateControl } from "@/components/inbox-state-control";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

function okResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200 });
}

describe("InboxStateControl", () => {
  beforeEach(() => {
    refresh.mockClear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("patches exactly the state field and refreshes the server-rendered view", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () => okResponse({ state: "applied" }));
    vi.stubGlobal("fetch", fetchMock);

    render(<InboxStateControl itemId="run_1:opp_1" state="new" />);
    await user.click(screen.getByRole("button", { name: /mark applied/i }));

    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/inbox/run_1%3Aopp_1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ state: "applied" }),
      }),
    );
  });

  it("blocks a second click while a request is in flight", async () => {
    const user = userEvent.setup();
    let resolveFetch: (value: Response) => void = () => {};
    const fetchMock = vi.fn(
      () => new Promise<Response>((resolve) => (resolveFetch = resolve)),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<InboxStateControl itemId="item_1" state="new" />);
    const button = screen.getByRole("button", { name: /mark applied/i });
    await user.click(button);
    await user.click(button);
    await user.click(screen.getByRole("button", { name: /dismiss/i }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    resolveFetch(okResponse({ state: "applied" }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it("reports a backend failure instead of pretending the state changed", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ error: "no inbox item item_1" }), {
          status: 404,
        }),
      ),
    );

    render(<InboxStateControl itemId="item_1" state="new" />);
    await user.click(screen.getByRole("button", { name: /dismiss/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "no inbox item item_1",
    );
    expect(refresh).not.toHaveBeenCalled();
  });

  it("offers a way back: a non-new item can be restored", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () => okResponse({ state: "new" }));
    vi.stubGlobal("fetch", fetchMock);

    render(<InboxStateControl itemId="item_1" state="dismissed" />);
    await user.click(screen.getByRole("button", { name: /restore to new/i }));

    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/inbox/item_1",
      expect.objectContaining({ body: JSON.stringify({ state: "new" }) }),
    );
  });

  it("does not offer the state the item is already in", () => {
    render(<InboxStateControl itemId="item_1" state="applied" />);

    expect(screen.queryByRole("button", { name: /mark applied/i })).toBeNull();
    expect(screen.getByRole("button", { name: /dismiss/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /restore to new/i }),
    ).toBeInTheDocument();
  });
});
