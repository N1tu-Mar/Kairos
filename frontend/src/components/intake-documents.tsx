"use client";

import { useRef, useState } from "react";

import { intakeFileProblem } from "@/lib/intake-route-contracts";
import type { IntakeDocument } from "@/lib/types";

interface Props {
  sessionId: string;
  documents: IntakeDocument[];
  disabled: boolean;
  onChanged: () => Promise<void>;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
}

async function errorMessage(response: Response, fallback: string): Promise<string> {
  const body = (await response.json().catch(() => null)) as { error?: unknown } | null;
  return typeof body?.error === "string" ? body.error : fallback;
}

export function IntakeDocuments({
  sessionId,
  documents,
  disabled,
  onChanged,
  onNotice,
  onError,
}: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const ready = documents.filter((document) => document.status === "ready");

  async function upload(file: File) {
    const problem = intakeFileProblem(file);
    if (problem) return onError(problem);
    if (ready.length >= 2) return onError("Remove a document before adding another.");
    setUploading(file.name);
    onError("");
    const form = new FormData();
    form.set("file", file, file.name);
    try {
      const response = await fetch(
        `/api/intake/${encodeURIComponent(sessionId)}/documents`,
        { method: "POST", body: form },
      );
      if (!response.ok) {
        throw new Error(await errorMessage(response, "The document could not be processed."));
      }
      await onChanged();
      onNotice(`${file.name} was extracted and added as evidence.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "The document could not be processed.");
    } finally {
      setUploading(null);
      if (input.current) input.current.value = "";
    }
  }

  async function remove(document: IntakeDocument) {
    setRemoving(document.document_id);
    onError("");
    try {
      const response = await fetch(
        `/api/intake/${encodeURIComponent(sessionId)}/documents/${encodeURIComponent(document.document_id)}`,
        { method: "DELETE" },
      );
      if (!response.ok) {
        throw new Error(await errorMessage(response, "The document could not be removed."));
      }
      await onChanged();
      onNotice(`${document.filename} was removed.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "The document could not be removed.");
    } finally {
      setRemoving(null);
    }
  }

  return (
    <section aria-labelledby="intake-documents-heading" className="border-t border-rule pt-5">
      <div className="flex items-baseline justify-between gap-3">
        <h4 id="intake-documents-heading" className="text-[11px] font-medium uppercase tracking-[0.12em] text-ink-muted">
          Documents
        </h4>
        <span className="font-mono text-[11px] text-ink-muted">{ready.length}/2</span>
      </div>
      <label
        className={`mt-2 block cursor-pointer rounded-lg border border-dashed px-3 py-4 text-center text-xs leading-relaxed transition-colors ${dragging ? "border-accent bg-canvas text-ink" : "border-rule text-ink-muted hover:border-accent"} ${disabled || ready.length >= 2 ? "cursor-not-allowed opacity-50" : ""}`}
        onDragEnter={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!disabled && event.dataTransfer.files[0]) void upload(event.dataTransfer.files[0]);
        }}
      >
        <span>{uploading ? `Extracting ${uploading}…` : "Drop a brief or pitch deck, or choose a file"}</span>
        <span className="mt-1 block text-[10px]">PDF, PPTX, TXT, or Markdown · 10 MB maximum</span>
        <input
          ref={input}
          type="file"
          accept=".pdf,.pptx,.txt,.md,.markdown,application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation,text/plain,text/markdown"
          className="sr-only"
          disabled={disabled || ready.length >= 2 || Boolean(uploading)}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
          }}
        />
      </label>
      {ready.length ? (
        <ul className="mt-3 space-y-2" aria-label="Uploaded intake documents">
          {ready.map((document) => (
            <li key={document.document_id} className="flex items-center justify-between gap-3 rounded-md bg-canvas px-3 py-2">
              <span className="min-w-0 truncate text-xs text-ink" title={document.filename}>{document.filename}</span>
              <button
                type="button"
                className="shrink-0 text-xs text-ink-muted underline underline-offset-2 hover:text-alert disabled:opacity-50"
                disabled={disabled || removing === document.document_id}
                onClick={() => void remove(document)}
              >
                {removing === document.document_id ? "Removing…" : "Remove"}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
