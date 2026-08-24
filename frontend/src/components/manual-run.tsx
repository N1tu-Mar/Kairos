"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { RunCounters } from "@/components/run-counters";
import { formatDuration } from "@/lib/format";
import type { RunReport } from "@/lib/types";

/**
 * "Run Kairos now" — a manual action, and presented as exactly that.
 *
 * Kairos is designed around a run that happens while the founder is asleep,
 * but nothing in this repository schedules one. This button does not create a
 * schedule, and the copy never implies it does. See `incomplete.md`.
 *
 * The pipeline reports no intermediate progress, so this shows elapsed time
 * and says what stage it *cannot* see, rather than animating a fake bar.
 */

type Status =
  | { phase: "idle" }
  | { phase: "running"; startedAt: number }
  | { phase: "done"; report: RunReport }
  | { phase: "error"; message: string; detail?: string };

export function ManualRunControl({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const [status, setStatus] = useState<Status>({ phase: "idle" });
  const [useDemoCatalog, setUseDemoCatalog] = useState(false);
  const [includeGrantsGov, setIncludeGrantsGov] = useState(true);
  const [elapsed, setElapsed] = useState(0);
  const inFlight = useRef(false);

  const running = status.phase === "running";

  useEffect(() => {
    if (status.phase !== "running") return;
    const startedAt = status.startedAt;
    setElapsed(0);
    const timer = setInterval(() => {
      setElapsed(Math.round((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [status]);

  async function start() {
    // Guarded twice: the ref closes the gap between click and re-render, the
    // disabled attribute covers everything after it.
    if (inFlight.current) return;
    inFlight.current = true;
    setStatus({ phase: "running", startedAt: Date.now() });

    try {
      const response = await fetch("/api/runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          use_demo_catalog: useDemoCatalog,
          include_grants_gov: includeGrantsGov,
        }),
      });

      const payload: unknown = await response.json().catch(() => null);

      if (!response.ok) {
        const body = (payload ?? {}) as { error?: string; detail?: string };
        setStatus({
          phase: "error",
          message:
            body.error ??
            `The backend returned ${response.status} and the run did not complete.`,
          detail: body.detail,
        });
        return;
      }

      setStatus({ phase: "done", report: payload as RunReport });
      // The server components on this page read the same data. Re-render them.
      router.refresh();
    } catch (error) {
      setStatus({
        phase: "error",
        message:
          "The request never reached the Kairos API. It may be down, or the connection dropped mid-run.",
        detail: error instanceof Error ? error.message : String(error),
      });
    } finally {
      inFlight.current = false;
    }
  }

  return (
    <div className="rounded-lg border border-rule bg-surface p-5 sm:p-6">
      <h2 className="font-serif text-lg tracking-tight text-ink">Run Kairos now</h2>
      <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
        Starts one run by hand, immediately. This does not schedule anything and
        nothing runs on its own afterwards — nothing in this repository schedules
        a run yet. A run discovers, filters, judges and drafts, so it can take
        minutes.
      </p>

      {!compact ? (
        <fieldset className="mt-4 space-y-2.5" disabled={running}>
          <legend className="sr-only">Run options</legend>
          <label className="flex items-start gap-2.5 text-sm text-ink-soft">
            <input
              type="checkbox"
              className="mt-1 accent-[var(--accent)]"
              checked={includeGrantsGov}
              onChange={(event) => setIncludeGrantsGov(event.target.checked)}
            />
            <span>
              Include Grants.gov
              <span className="block text-xs text-ink-muted">
                Live API call. Turn it off to run against the local catalog only.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2.5 text-sm text-ink-soft">
            <input
              type="checkbox"
              className="mt-1 accent-[var(--accent)]"
              checked={useDemoCatalog}
              onChange={(event) => setUseDemoCatalog(event.target.checked)}
            />
            <span>
              Use the demo catalog
              <span className="block text-xs text-ink-muted">
                Synthetic rows, every title marked{" "}
                <span className="font-mono">[DEMO]</span> and every URL on an
                unresolvable domain.
              </span>
            </span>
          </label>
        </fieldset>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={start}
          disabled={running}
          aria-busy={running}
          className="rounded-md bg-ink px-4 py-2 text-sm font-medium text-paper transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {running ? "Running…" : "Run Kairos now"}
        </button>

        {running ? (
          <p role="status" aria-live="polite" className="text-sm text-ink-muted">
            <span className="kairos-pulse">Running</span> · {formatDuration(elapsed)}{" "}
            elapsed. The pipeline does not report progress mid-run, so there is
            nothing honest to show until it returns.
          </p>
        ) : null}
      </div>

      {status.phase === "error" ? (
        <div
          role="alert"
          className="mt-5 rounded-md border border-alert/40 bg-alert-soft px-4 py-3"
        >
          <p className="text-sm font-medium text-ink">The run did not complete</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-soft">
            {status.message}
          </p>
          {status.detail ? (
            <p className="mt-2 break-words font-mono text-xs text-ink-muted">
              {status.detail}
            </p>
          ) : null}
          <p className="mt-2 text-xs leading-relaxed text-ink-muted">
            If the request timed out, the backend may still be working. Check the
            run history before starting another one.
          </p>
        </div>
      ) : null}

      {status.phase === "done" ? (
        <div className="mt-5 space-y-3 border-t border-rule pt-5">
          <p className="text-sm text-ink-soft">
            Run <span className="font-mono text-xs">{status.report.run_id}</span>{" "}
            finished in {formatDuration(status.report.duration_s)}.
          </p>
          <RunCounters report={status.report} size="small" />
          {status.report.halted_reason ? (
            <p className="text-sm text-alert">
              Halted: {status.report.halted_reason}
            </p>
          ) : null}
          {status.report.surfaced === 0 && !status.report.halted_reason ? (
            <p className="text-sm text-ink-muted">
              Nothing surfaced. That is a legitimate result — the reasons are on
              the run detail page.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
