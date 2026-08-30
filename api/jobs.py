"""The durable job boundary between HTTP and the pipeline.

`POST /founders/{id}/runs` used to hold the connection open through
discovery, every model call, and drafting — minutes of work living or dying
with one TCP socket. Now the endpoint persists a `RunJob`, hands it to an
executor, and answers 202. The job row is the source of truth from that
moment: a poller reads it, a retry with the same idempotency key resolves to
it, and a crash is repaired by marking it failed at startup rather than
leaving it "running" forever.

`LocalJobExecutor` runs the job as an asyncio task in the API process. That
is the local mode and the current production mode — one Fargate task, one
process, the run lease making sure there is never a second run anyway. The
`JobExecutor` protocol is the seam for a real queue: a production adapter
would enqueue the job id (SQS or similar), a separate worker process would
claim it, take the same lease, call the same `execute_job`, and write the
same rows. Nothing in this module assumes which side of that seam it is on
except `LocalJobExecutor` itself.

Failure classes recorded to the scheduler failure log:

*   `startup`   — the run could not begin (config, sub-agent construction)
*   `timeout`   — the run outlived `KAIROS_RUN_TIMEOUT_S` and was cancelled
*   `crash`     — an unhandled exception escaped the pipeline
*   `orphaned`  — the process died and startup recovery found the row

A run that *completes* with `halted_reason` set (budget cap, throttle,
abstention) is a finished run with a report, not a failure — the job says
`halted` and points at the report, and nothing goes to the failure log.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol

from agent.budget import RunBudget
from agent.config import REPO_ROOT, settings
from agent.models import ApplicationForm, RunJob
from agent.runtime import SubAgents
from agent.scheduler import Lease, RunLock, ScheduledRunFailureLog
from agent.sanitize import safe_detail
from agent.scout import new_run_context, run_once
from agent.tools.campus import CampusDiscoverySource, reviewed_web_sources
from agent.tools.discovery import GrantsGovClient, GrantsGovSource, SeedCatalog

log = logging.getLogger("kairos.jobs")

#: The one run kind the pipeline has. Manual and scheduled invocations share
#: it on purpose: the lease exists so two *runs* never overlap, whoever asked.
RUN_KIND = "pipeline"


def _now() -> datetime:
    """Timezone-aware UTC now. Job timestamps are compared against each other and against lease expiries, so a naive value here would raise at comparison time."""
    return datetime.now(timezone.utc)


def new_job(
    *,
    founder_id: str,
    idempotency_key: str | None,
    source: str,
    use_demo_catalog: bool,
    include_grants_gov: bool,
) -> RunJob:
    """Build an unpersisted `RunJob` in the `queued` state.

    The caller persists it — that write is what makes the job real and what
    enforces idempotency, because the unique index on `founder_id::key` is in
    the database rather than in a check here. Two concurrent requests with the
    same key both get a job object out of this function; only one of them
    survives `save_job`.
    """
    return RunJob(
        job_id=f"job_{uuid.uuid4().hex[:12]}",
        founder_id=founder_id,
        idempotency_key=idempotency_key,
        source=source,  # type: ignore[arg-type]
        use_demo_catalog=use_demo_catalog,
        include_grants_gov=include_grants_gov,
    )


def load_forms() -> dict[str, ApplicationForm]:
    """Load every transcribed application form, keyed by opportunity id.

    Read fresh on each job rather than cached at import, so editing a form
    JSON takes effect on the next run without a restart. A form whose JSON
    fails validation raises here and is reported as a `startup` failure — the
    run does not silently proceed with the form missing.

    Only one form per opportunity survives: later files with the same
    `opportunity_id` overwrite earlier ones in glob order.
    """
    import json

    directory = REPO_ROOT / "data" / "forms"
    if not directory.exists():
        return {}
    forms = {}
    for path in sorted(directory.glob("*.json")):
        form = ApplicationForm.model_validate(json.loads(path.read_text()))
        forms[form.opportunity_id] = form
    return forms


def build_sources(job: RunJob):
    """Assemble the discovery sources for one job, in priority order.

    Seed catalog always; Grants.gov only when the job asked for it; campus
    always, but gated twice — `enable_browser` decides whether it yields
    anything at all, and `allow_live_scrape=False` means it can only read rows
    a person already accepted. A job never crawls.
    """
    config = settings()
    catalog = "opportunities.demo.json" if job.use_demo_catalog else "opportunities.seed.json"
    sources = [
        SeedCatalog(
            config.data_dir / catalog,
            # The demo catalog is synthetic and unverified by construction,
            # so loading it at all is an explicit opt-in.
            allow_unverified=job.use_demo_catalog or config.allow_unverified_seed,
        )
    ]
    if job.include_grants_gov:
        sources.append(
            GrantsGovSource(
                GrantsGovClient(config.grants_gov_base_url, config.http_timeout_s)
            )
        )
    # The same Tier 3 source the CLI builds. Without this line the flag would
    # mean one thing from a terminal and another from the dashboard, which is
    # worse than the flag not existing. No live sweep is ever run from a job:
    # a scheduled request must not start a crawl.
    sources.append(
        CampusDiscoverySource(enabled=config.enable_browser, allow_live_scrape=False)
    )
    sources.extend(reviewed_web_sources())
    return sources


class JobExecutor(Protocol):
    """The seam between accepting a job and running it.

    `LocalJobExecutor` is the in-process implementation. A queue-backed one
    would enqueue the job id in `submit`, and `cancel` would have to reach
    across processes — which is why `cancel` returns a bool rather than
    asserting success. `False` means "not running here", not "not running".
    """

    #: Start the job. Takes ownership of `lease` — the executor releases it.
    def submit(self, job: RunJob, lease: Lease) -> None: ...

    #: Try to cancel. False when this executor is not running that job.
    def cancel(self, job_id: str) -> bool: ...


async def execute_job(job: RunJob, repo, lease: Lease, failure_log: ScheduledRunFailureLog) -> None:
    """Run one job to a terminal state. The lease is released no matter what.

    This function is executor-agnostic on purpose — a queue-backed worker
    calls exactly this. Every exit path writes a terminal status before the
    `finally` releases the lease, so a poller can never observe a free lease
    with a job still claiming to run.
    """
    timeout_s = settings().run_timeout_s
    try:
        job.status = "running"
        job.started_at = _now()
        repo.save_job(job)

        try:
            profile = repo.get_profile(job.founder_id)
            if profile is None:
                raise RuntimeError(f"no profile for {job.founder_id}")
            budget = RunBudget.from_settings(settings())
            # A scheduled run calls real models. If a dollar cap is
            # configured that zero prices make unenforceable, refuse here —
            # before the first token — rather than running to completion
            # under a cap that was never going to trip.
            budget.require_enforceable_spend_cap()
            ctx = new_run_context(
                profile=profile,
                repo=repo,
                budget=budget,
                agents=SubAgents.build(),
            )
            ctx.forms = load_forms()
            sources = build_sources(job)
        except Exception as exc:  # noqa: BLE001 — a start failure is a class of its own
            _fail(job, repo, failure_log, exc, failure_class="startup")
            return

        try:
            report = await asyncio.wait_for(run_once(ctx, sources), timeout=timeout_s)
        except asyncio.TimeoutError:
            job.status = "failed"
            job.error = f"run exceeded the {timeout_s:.0f}s timeout and was cancelled"
            job.finished_at = _now()
            repo.save_job(job)
            failure_log.record(
                founder_id=job.founder_id,
                detail=job.error,
                source=job.source,
                failure_class="timeout",
            )
            return
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.error = "cancelled while running"
            job.finished_at = _now()
            repo.save_job(job)
            raise
        except Exception as exc:  # noqa: BLE001
            # run_once catches pipeline exceptions into halted_reason itself;
            # anything that still escapes is infrastructure, not judgment.
            _fail(job, repo, failure_log, exc, failure_class="crash")
            return

        job.run_id = report.run_id
        job.status = "halted" if report.halted_reason else "succeeded"
        job.finished_at = _now()
        repo.save_job(job)
    finally:
        lease.release()


def _fail(job: RunJob, repo, failure_log, exc: Exception, *, failure_class: str) -> None:
    """Write the terminal `failed` state and log it under `failure_class`.

    Called from the paths where the run never produced a report. A run that
    finishes with `halted_reason` is not a failure and does not come through
    here.
    """
    job.status = "failed"
    # Sanitised here, not only on the way to the failure log: this string is
    # persisted and served by GET /founders/{id}/jobs/{job_id}. An exception
    # message carries whatever was nearby — a ledger path, a config key, a
    # pydantic ValidationError quoting the model's raw output back at you.
    job.error = safe_detail(f"{type(exc).__name__}: {exc}", limit=300)
    job.finished_at = _now()
    repo.save_job(job)
    failure_log.record(
        founder_id=job.founder_id,
        detail=job.error,
        source=job.source,
        failure_class=failure_class,
    )
    log.exception("job_failed", extra={"job_id": job.job_id, "class": failure_class})


class LocalJobExecutor:
    """Runs jobs as asyncio tasks in the API process.

    Correct while there is one process — which the run lease and the
    single-task ECS service both guarantee. The moment a second worker
    exists, this class is the thing to replace, not `execute_job`.
    """

    def __init__(self, repo, failure_log: ScheduledRunFailureLog) -> None:
        """`_tasks` maps job id to the running asyncio task, and is the only thing that makes `cancel` possible — it is in-process state, so it is empty after a restart and `recover_orphaned_jobs` is what cleans up instead."""
        self.repo = repo
        self.failure_log = failure_log
        self._tasks: dict[str, asyncio.Task] = {}

    def submit(self, job: RunJob, lease: Lease) -> None:
        """Fire the job as a background asyncio task and return immediately.

        The done-callback drops the task from `_tasks` on every terminal path,
        including cancellation, so the map cannot grow without bound. Nothing
        awaits the task: its result and its failure are both recorded in the job
        row by `execute_job`, which is the point of the durable boundary.
        """
        task = asyncio.create_task(
            execute_job(job, self.repo, lease, self.failure_log),
            name=f"kairos-{job.job_id}",
        )
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.job_id, None))

    def cancel(self, job_id: str) -> bool:
        """Cancel a job this process is running. False if it is not here.

        Cancellation is cooperative and terminal: the task's CancelledError
        handler writes `cancelled` before propagating. Work the run already
        persisted (opportunities, the report of a completed run) stays —
        cancel stops future work, it does not un-happen the past.
        """
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True


def recover_orphaned_jobs(repo, failure_log: ScheduledRunFailureLog, run_lock: RunLock) -> int:
    """Startup repair: no crash may leave a job 'running' forever.

    Every queued/running row belongs to a process that no longer exists —
    this function runs before the executor accepts anything new. Each one is
    marked failed and logged. The dead process's lease, if any, expires on
    its own TTL; we do not force-release it because we cannot prove the row
    and the lease belonged to the same invocation.
    """
    orphaned = repo.fail_orphaned_jobs(
        "the API process restarted while this job was in flight"
    )
    for job in orphaned:
        failure_log.record(
            founder_id=job.founder_id,
            detail=f"job {job.job_id} was orphaned by a process restart",
            source=job.source,
            failure_class="orphaned",
        )
    if orphaned:
        log.warning("orphaned_jobs_recovered", extra={"count": len(orphaned)})
    return len(orphaned)
