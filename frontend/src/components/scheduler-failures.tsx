import { formatRelative } from "@/lib/format";
import type { SchedulerFailure } from "@/lib/types";

/**
 * Invocations that failed to *start or finish*, newest first.
 *
 * A run that halted on a budget cap is not here — that run finished, has a
 * report, and appears in the run history like any other. This panel exists
 * for the case with no run report at all: the scheduler fired and nothing
 * happened. Without it, "Kairos has been quiet" and "Kairos has been broken
 * for four days" look identical on the briefing.
 *
 * Every field is sanitised server-side before it is persisted — no
 * credentials, no prompts, no stack traces. This component adds no formatting
 * that could reconstruct any of them.
 */

const FAILURE_CLASS_COPY: Record<string, string> = {
  startup: "The run could not be started.",
  timeout: "The run passed its time limit and was stopped.",
  crash: "The run stopped on an unexpected error.",
  orphaned: "The backend restarted while this run was in flight.",
};

/**
 * Recent invocations that failed to start or finish, newest first.
 *
 * Details are sanitised on the backend before they are persisted, so what
 * reaches here carries no credentials, prompts or stack traces.
 */
export function SchedulerFailures({
  failures,
}: {
  failures: SchedulerFailure[];
}) {
  if (failures.length === 0) return null;

  return (
    <div
      role="alert"
      className="rounded-lg border border-alert/40 bg-alert-soft px-5 py-4 sm:px-6"
    >
      <p className="font-serif text-lg text-ink">
        {failures.length === 1
          ? "A run did not complete"
          : `${failures.length} runs did not complete`}
      </p>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-soft">
        These invocations produced no run at all, not a run that found nothing
        and not a run that stopped at a cap. Nothing was searched.
      </p>

      <ul className="mt-4 space-y-3">
        {failures.map((failure, index) => (
          <li
            key={`${failure.at}-${index}`}
            className="border-t border-alert/20 pt-3 first:border-t-0 first:pt-0"
          >
            <p className="text-sm text-ink">
              {FAILURE_CLASS_COPY[failure.failure_class] ??
                "The run did not complete."}{" "}
              <span className="text-ink-muted">
                {formatRelative(failure.at)}
                {failure.source === "scheduled" ? " · scheduled" : null}
                {failure.source === "manual" ? " · started by hand" : null}
                {failure.retry_count > 0
                  ? ` · retry ${failure.retry_count}`
                  : null}
              </span>
            </p>
            {failure.detail ? (
              <p className="mt-1 break-words font-mono text-xs leading-relaxed text-ink-muted">
                {failure.detail}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
