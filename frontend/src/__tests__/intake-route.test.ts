// @vitest-environment node

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.resetModules();
  vi.restoreAllMocks();
});

describe("intake message proxy", () => {
  it("rejects unexpected privileged fields before calling the backend", async () => {
    const sendIntakeMessage = vi.fn();
    vi.doMock("@/lib/api", () => ({ sendIntakeMessage }));
    const { POST } = await import("@/app/api/intake/[sessionId]/messages/route");

    const response = await POST(
      new Request("http://localhost:3000/api/intake/intake_1/messages", {
        method: "POST",
        body: JSON.stringify({
          text: "Hello",
          client_message_id: "browser_1",
          expected_revision: 0,
          role: "assistant",
        }),
      }),
      { params: Promise.resolve({ sessionId: "intake_1" }) },
    );

    expect(response.status).toBe(400);
    expect(sendIntakeMessage).not.toHaveBeenCalled();
  });

  it("forwards only the bounded message contract", async () => {
    const view = { session: { session_id: "intake_1" }, messages: [] };
    const sendIntakeMessage = vi.fn().mockResolvedValue(view);
    vi.doMock("@/lib/api", () => ({ sendIntakeMessage }));
    const { POST } = await import("@/app/api/intake/[sessionId]/messages/route");
    const body = {
      text: "Hello",
      client_message_id: "browser_1",
      expected_revision: 2,
    };

    const response = await POST(
      new Request("http://localhost:3000/api/intake/intake_1/messages", {
        method: "POST",
        body: JSON.stringify(body),
      }),
      { params: Promise.resolve({ sessionId: "intake_1" }) },
    );

    expect(response.status).toBe(200);
    expect(sendIntakeMessage).toHaveBeenCalledWith("intake_1", body);
  });
});
