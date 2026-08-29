"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { RunCounters } from "@/components/run-counters";
import { formatDuration } from "@/lib/format";
import type { JobStatusResponse, RunJob, RunReport } from "@/lib/types";

/**
 * "Run Kairos now" — a manual action, and presented as exactly that.
 *
 * The backend accepts the run and answers immediately with a job; the run
 * itself happens there, on its own, and survives this tab being closed. This
 * component polls the job until it reaches a terminal state.
 *
 * Every click carries a fresh idempotency key, so a double-submit or a
 * retried request resolves to the *same* run rather than starting a second
 * one. A 409 means a run is already in progress — which is a normal answer,
 * not a failure, and is worded as one.
 *
 * The pipeline still reports no intermediate progress, so this shows elapsed
 * time and says what stage it *cannot* see, rather than animating a fake bar.
 */

/** How often to ask the backend whether the run has finished. */
const POLL_INTERVAL_MS = 2000;

/** Consecutive failed polls before the UI stops claiming to know anything. */
const MAX_POLL_FAILURES = 5;

type Status =
  | { phase: "idle" }
  | { phase: "running"; startedAt: number; job: RunJob }
  | { phase: "done"; job: RunJob; report: RunReport | null }
  | { phase: "conflict"; message: string }
  | { phase: "error"; message: string; detail?: string };

/**
 * A fresh key per click, so a double-submit resolves to one run.
 *
 * The non-crypto fallback is weaker but only has to be unique among one
 * user's clicks, and it exists so the component still works in a test
 * environment without `crypto.randomUUID`.
 */
function newIdempotencyKey(): string {
  // crypto.randomUUID is available in every browser this app supports; the
  // fallback keeps the component usable in a test environment without it.
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `manual-${crypto.randomUUID()}`;
  }
  return `manual-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * The manual run button, its options, and the live status of the run it started.
 *
 * State lives in one `Status` union rather than several booleans, so
 * impossible combinations (running *and* errored) cannot be represented.
 */
export function ManualRunControl({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const [status, setStatus] = useState<Status>({ phase: "idle" });
  const [useDemoCatalog, setUseDemoCatalog] = useState(false);
  const [includeGrantsGov, setIncludeGrantsGov] = useState(true);
  // A clock tick, not the elapsed value itself. `elapsed` is derived below,
  // so a new run starts at 0 without an effect having to reset it.
  const [now, setNow] = useState<number | null>(null);
  const inFlight = useRef(false);

  const running = status.phase === "running";
  const jobId = running ? status.job.job_id : null;

  useEffect(() => {
    if (status.phase !== "running") return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [status.phase]);

  // Derived, never assigned. `now` is stale from the previous run when a new
  // one starts, which makes the difference negative — hence the clamp, which
  // is what resets the display to 0.
  const elapsed =
    status.phase === "running" && now !== null
      ? Math.max(0, Math.round((now - status.startedAt) / 1000))
      : 0;

  const finish = useCallback(
    (job: RunJob, report: RunReport | null) => {
      inFlight.current = false;
      setStatus({ phase: "done", job, report });
      // The server components on this page read the same data. Re-render them.
      router.refresh();
    },
    [router],
  );

  // Poll until the job is terminal. Transient poll failures are tolerated —
  // the run is on the backend and a dropped request says nothing about it.
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let failures = 0;

    /**
     * Ask the backend once whether the job is terminal, and finish if it is.
     *
     * Transient failures are counted, not surfaced: the run lives on the
     * backend, so a dropped request says nothing about it. Only after
     * `MAX_POLL_FAILURES` consecutive failures does the UI admit it has lost
     * track — and it says the run may still be going rather than claiming it
     * failed.
     *
     * `cancelled` is checked after every await so a poll that resolves after
     * the effect was torn down cannot write state.
     */
    async function poll() {
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(jobId!)}`);
        if (!response.ok) throw new Error(`status ${response.status}`);
        const body = (await response.json()) as JobStatusResponse;
        if (cancelled) return;
        failures = 0;

        const terminal =
          body.job.status === "succeeded" ||
          body.job.status === "halted" ||
          body.job.status === "failed" ||
          body.job.status === "cancelled";
        if (terminal) finish(body.job, body.report);
      } catch (error) {
        if (cancelled) return;
        failures += 1;
        if (failures >= MAX_POLL_FAILURES) {
          inFlight.current = false;
          setStatus({
            phase: "error",
            message:
              "Lost contact with the Kairos API while the run was in progress. The run may still be going, so check the run history.",
            detail: error instanceof Error ? error.message : String(error),
          });
        }
      }
    }

    const timer = setInterval(poll, POLL_INTERVAL_MS);
    void poll();
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [jobId, finish]);

  /**
   * POST the run and move into the running phase, or into conflict/error.
   *
   * Guarded twice against a double-click: the `inFlight` ref closes the
   * window between the click and the re-render, and the disabled attribute
   * covers everything after it.
   *
   * 409 is handled as its own phase rather than as an error — a run already
   * being in progress is a correct answer about a real run.
   */
  async function start() {
    // Guarded twice: the ref closes the gap between click and re-render, the
    // disabled attribute covers everything after it.
    if (inFlight.current) return;
    inFlight.current = true;

    try {
      const response = await fetch("/api/runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          use_demo_catalog: useDemoCatalog,
          include_grants_gov: includeGrantsGov,
          idempotency_key: newIdempotencyKey(),
        }),
      });

      const payload: unknown = await response.json().catch(() => null);

      if (response.status === 409) {
        const body = (payload ?? {}) as { error?: string };
        inFlight.current = false;
        setStatus({
          phase: "conflict",
          message:
            body.error ??
            "A run is already in progress for this founder. Kairos runs one at a time.",
        });
        return;
      }

      if (!response.ok) {
        const body = (payload ?? {}) as { error?: string; detail?: string };
        inFlight.current = false;
        setStatus({
          phase: "error",
          message:
            body.error ??
            `The backend returned ${response.status} and the run did not start.`,
          detail: body.detail,
        });
        return;
      }

      setStatus({
        phase: "running",
        startedAt: Date.now(),
        job: payload as RunJob,
      });
    } catch (error) {
      inFlight.current = false;
      setStatus({
        phase: "error",
        message:
          "The request never reached the Kairos API. It may be down, or the connection dropped.",
        detail: error instanceof Error ? error.message : String(error),
      });
    }
  }

  return (
    <div className="rounded-lg border border-rule bg-surface p-5 sm:p-6">
      <h2 className="font-serif text-lg tracking-tight text-ink">Run Kairos now</h2>
      <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
        Starts one run by hand, immediately. The run happens on the backend, so
        it keeps going if you close this page. This does not create a schedule.
        Production scheduling calls the same endpoint on a timer.
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
            nothing honest to show until it finishes.
          </p>
        ) : null}
      </div>

      {status.phase === "conflict" ? (
        <div className="mt-5 rounded-md border border-rule bg-paper px-4 py-3">
          <p className="text-sm font-medium text-ink">Already running</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-soft">
            {status.message}
          </p>
          <p className="mt-2 text-xs leading-relaxed text-ink-muted">
            Kairos holds one run lease per founder so two runs cannot duplicate
            work or race each other&apos;s spend.
          </p>
        </div>
      ) : null}

      {status.phase === "error" ? (
        <div
          role="alert"
          className="mt-5 rounded-md border border-alert/40 bg-alert-soft px-4 py-3"
        >
          <p className="text-sm font-medium text-ink">The run did not start</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-soft">
            {status.message}
          </p>
          {status.detail ? (
            <p className="mt-2 break-words font-mono text-xs text-ink-muted">
              {status.detail}
            </p>
          ) : null}
        </div>
      ) : null}

      {status.phase === "done" && status.job.status === "failed" ? (
        <div
          role="alert"
          className="mt-5 rounded-md border border-alert/40 bg-alert-soft px-4 py-3"
        >
          <p className="text-sm font-medium text-ink">The run failed</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-soft">
            {status.job.error ??
              "The backend recorded a failure but no reason. Check the logs."}
          </p>
        </div>
      ) : null}

      {status.phase === "done" && status.job.status === "cancelled" ? (
        <div className="mt-5 rounded-md border border-rule bg-paper px-4 py-3">
          <p className="text-sm text-ink-soft">
            The run was cancelled. Anything it had already recorded is kept.
          </p>
        </div>
      ) : null}

      {status.phase === "done" && status.report ? (
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
              Nothing surfaced. That is a legitimate result, and the reasons are
              on the run detail page.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
