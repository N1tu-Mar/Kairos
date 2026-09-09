// @vitest-environment node

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.resetModules();
  vi.restoreAllMocks();
});

function request(url: string, method: string, body: unknown): Request {
  return new Request(url, {
    method,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("intake control proxies", () => {
  it("forwards only the exact proposal-batch revision contract", async () => {
    const confirmIntakeBatch = vi.fn().mockResolvedValue({ session: { revision: 4 } });
    vi.doMock("@/lib/api", () => ({ confirmIntakeBatch }));
    const { POST } = await import(
      "@/app/api/intake/[sessionId]/proposal-batches/[batchId]/confirm/route"
    );

    const rejected = await POST(
      request("http://localhost/api/intake/s/proposal-batches/b/confirm", "POST", {
        expected_revision: 3,
        role: "admin",
      }),
      { params: Promise.resolve({ sessionId: "session_1", batchId: "batch_1" }) },
    );
    expect(rejected.status).toBe(400);
    expect(confirmIntakeBatch).not.toHaveBeenCalled();

    const accepted = await POST(
      request("http://localhost/api/intake/s/proposal-batches/b/confirm", "POST", {
        expected_revision: 3,
      }),
      { params: Promise.resolve({ sessionId: "session_1", batchId: "batch_1" }) },
    );
    expect(accepted.status).toBe(200);
    expect(confirmIntakeBatch).toHaveBeenCalledWith("session_1", "batch_1", 3);
  });

  it("rejects mass assignment and unknown structured fields", async () => {
    const updateIntakeField = vi.fn();
    vi.doMock("@/lib/api", () => ({ updateIntakeField }));
    const { PATCH } = await import(
      "@/app/api/intake/[sessionId]/fields/[field]/route"
    );

    const unknown = await PATCH(
      request("http://localhost/api/intake/s/fields/role", "PATCH", {
        action: "correct",
        expected_revision: 1,
        value: "admin",
      }),
      { params: Promise.resolve({ sessionId: "session_1", field: "role" }) },
    );
    expect(unknown.status).toBe(400);

    const extra = await PATCH(
      request("http://localhost/api/intake/s/fields/team_size", "PATCH", {
        action: "confirm",
        expected_revision: 1,
        isPaid: true,
      }),
      { params: Promise.resolve({ sessionId: "session_1", field: "team_size" }) },
    );
    expect(extra.status).toBe(400);
    expect(updateIntakeField).not.toHaveBeenCalled();
  });

  it("requires a bounded founder correction for a narrative claim", async () => {
    const updateIntakeClaim = vi.fn().mockResolvedValue({ session: { revision: 2 } });
    vi.doMock("@/lib/api", () => ({ updateIntakeClaim }));
    const { PATCH } = await import(
      "@/app/api/intake/[sessionId]/claims/[claimId]/route"
    );
    const body = {
      action: "correct",
      expected_revision: 1,
      text: "We serve campus laboratories.",
      category: "customers",
      client_action_id: "web-action-1",
    };

    const response = await PATCH(
      request("http://localhost/api/intake/s/claims/c", "PATCH", body),
      { params: Promise.resolve({ sessionId: "session_1", claimId: "claim_1" }) },
    );
    expect(response.status).toBe(200);
    expect(updateIntakeClaim).toHaveBeenCalledWith("session_1", "claim_1", body);
  });

  it("rebuilds multipart data with exactly one preliminarily valid file", async () => {
    const uploadIntakeDocument = vi.fn().mockResolvedValue({ document_id: "document_1" });
    vi.doMock("@/lib/api", () => ({ uploadIntakeDocument }));
    const { POST } = await import(
      "@/app/api/intake/[sessionId]/documents/route"
    );
    const form = new FormData();
    const file = new File(["Founder brief"], "brief.md", { type: "text/markdown" });
    form.set("file", file);
    const response = await POST(
      new Request("http://localhost/api/intake/session_1/documents", {
        method: "POST",
        body: form,
      }),
      { params: Promise.resolve({ sessionId: "session_1" }) },
    );

    expect(response.status).toBe(200);
    expect(uploadIntakeDocument).toHaveBeenCalledTimes(1);
    expect(uploadIntakeDocument.mock.calls[0]![0]).toBe("session_1");
    expect(uploadIntakeDocument.mock.calls[0]![1]).toBeInstanceOf(File);
  });

  it("rejects mismatched upload types and extra multipart fields", async () => {
    const uploadIntakeDocument = vi.fn();
    vi.doMock("@/lib/api", () => ({ uploadIntakeDocument }));
    const { POST } = await import(
      "@/app/api/intake/[sessionId]/documents/route"
    );
    const mismatch = new FormData();
    mismatch.set("file", new File(["not pdf"], "brief.pdf", { type: "text/plain" }));
    const mismatchResponse = await POST(
      new Request("http://localhost/api/intake/session_1/documents", {
        method: "POST",
        body: mismatch,
      }),
      { params: Promise.resolve({ sessionId: "session_1" }) },
    );
    expect(mismatchResponse.status).toBe(400);

    const extra = new FormData();
    extra.set("file", new File(["brief"], "brief.txt", { type: "text/plain" }));
    extra.set("role", "admin");
    const extraResponse = await POST(
      new Request("http://localhost/api/intake/session_1/documents", {
        method: "POST",
        body: extra,
      }),
      { params: Promise.resolve({ sessionId: "session_1" }) },
    );
    expect(extraResponse.status).toBe(400);
    expect(uploadIntakeDocument).not.toHaveBeenCalled();
  });

  it("forwards completion and removal without broad request objects", async () => {
    const completeIntake = vi.fn().mockResolvedValue({ founder_id: "founder_demo" });
    vi.doMock("@/lib/api", () => ({ completeIntake }));
    const completeRoute = await import(
      "@/app/api/intake/[sessionId]/complete/route"
    );
    const completed = await completeRoute.POST(
      request("http://localhost/api/intake/session_1/complete", "POST", {
        expected_revision: 6,
      }),
      { params: Promise.resolve({ sessionId: "session_1" }) },
    );
    expect(completed.status).toBe(200);
    expect(completeIntake).toHaveBeenCalledWith("session_1", 6);

    vi.resetModules();
    const removeIntakeDocument = vi.fn().mockResolvedValue(undefined);
    vi.doMock("@/lib/api", () => ({ removeIntakeDocument }));
    const removeRoute = await import(
      "@/app/api/intake/[sessionId]/documents/[documentId]/route"
    );
    const removed = await removeRoute.DELETE(
      new Request("http://localhost/api/intake/session_1/documents/document_1"),
      {
        params: Promise.resolve({
          sessionId: "session_1",
          documentId: "document_1",
        }),
      },
    );
    expect(removed.status).toBe(204);
    expect(removeIntakeDocument).toHaveBeenCalledWith("session_1", "document_1");
  });
});
