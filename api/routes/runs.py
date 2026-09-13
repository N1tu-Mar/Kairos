"""Run reports, run triggering, job polling and cancellation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent.models import RunJob, RunReport
from api import jobs as job_module
from api.auth import SCOPE_RUN_CANCEL, SCOPE_RUN_TRIGGER, Principal, audit_event
from api.deps import (
    ListLimit,
    ResourceId,
    enforce_rate_limit,
    limit_authenticated_write,
    owned,
    principal,
    require_paid_work_capacity,
)
from api.schemas import RunTrigger

router = APIRouter()


def _skips_payload(report: RunReport) -> dict:
    """Everything a run threw away, in one shape.

    Shared by the `latest` and by-id routes so the two can never drift into
    telling different stories about the same run.
    """
    return {
        "run_id": report.run_id,
        "headline": report.headline(),
        "rejections": report.rejections,
        "skips": report.skips,
        "sources_failed": report.sources_failed,
        "notes": report.notes,
    }


def _run_for_founder(state, founder_id: str, run_id: str) -> RunReport:
    """One run, scoped to the founder in the path.

    Now a security control, not just a typo guard: the caller has already
    been authorized for `founder_id`, and a run belonging to anyone else is
    a 404 regardless of whether the id was guessed or mistyped.
    """
    report = state.repo.get_run(run_id)
    if report is None or report.founder_id != founder_id:
        raise HTTPException(404, f"no run {run_id} for {founder_id}")
    return report


@router.get("/founders/{founder_id}/runs")
def list_runs(
    request: Request,
    founder_id: ResourceId,
    limit: ListLimit = 20,
    actor: Principal = Depends(principal),
) -> list[RunReport]:
    """Recent run reports for one founder, newest first, capped at `limit`."""
    state = request.app.state
    owned(founder_id, actor)
    return state.repo.list_runs(founder_id, limit)


@router.get("/founders/{founder_id}/runs/latest")
def latest_run(request: Request, founder_id: ResourceId, actor: Principal = Depends(principal)) -> RunReport:
    """The most recent run report. 404 when the founder has never had a run."""
    state = request.app.state
    owned(founder_id, actor)
    report = state.repo.latest_run(founder_id)
    if report is None:
        raise HTTPException(404, f"no runs recorded for {founder_id}")
    return report


@router.get("/founders/{founder_id}/runs/latest/skips")
def latest_skips(request: Request, founder_id: ResourceId, actor: Principal = Depends(principal)) -> dict:
    """Everything the agent threw away, and why.

    The founder does not see this by default. A judge asking "how do I know
    it isn't hiding things?" gets it in one click.
    """
    state = request.app.state
    owned(founder_id, actor)
    report = state.repo.latest_run(founder_id)
    if report is None:
        raise HTTPException(404, f"no runs recorded for {founder_id}")
    return _skips_payload(report)


@router.get("/founders/{founder_id}/runs/{run_id}")
def get_run(
    request: Request,
    founder_id: ResourceId, run_id: ResourceId, actor: Principal = Depends(principal)
) -> RunReport:
    """One run by id, however old.

    `list_runs` is capped, so without this a link to an older run resolves to
    nothing and the transparency trail has a horizon.
    """
    state = request.app.state
    owned(founder_id, actor)
    return _run_for_founder(state, founder_id, run_id)


@router.get("/founders/{founder_id}/runs/{run_id}/skips")
def get_run_skips(
    request: Request,
    founder_id: ResourceId, run_id: ResourceId, actor: Principal = Depends(principal)
) -> dict:
    """The silent path for one specific run."""
    state = request.app.state
    owned(founder_id, actor)
    return _skips_payload(_run_for_founder(state, founder_id, run_id))


@router.post("/founders/{founder_id}/runs", status_code=202)
async def trigger_run(
    request: Request,
    founder_id: ResourceId,
    trigger: RunTrigger,
    response: Response,
    actor: Principal = Depends(principal),
) -> RunJob:
    """Accept a run and return immediately. EventBridge calls this too.

    Three outcomes, all fast:

    *   **202** — a job was created and is now running in the background.
        Poll `GET /founders/{id}/jobs/{job_id}` for its state.
    *   **200** — this idempotency key already landed; here is that job
        again. A scheduler retry or a double-submitted form resolves to the
        same logical invocation instead of a second run.
    *   **409** — another run holds the lease for this founder. The body
        names the running job when it is known.

    The connection no longer spans the run. A run takes minutes; sockets,
    load balancers and browsers all have opinions about minutes.
    """
    state = request.app.state
    owned(founder_id, actor, write=True, scope=SCOPE_RUN_TRIGGER)
    profile = state.repo.get_profile(founder_id)
    if profile is None:
        raise HTTPException(404, f"no profile for {founder_id}")

    # A scheduler credential is not a founder. Force the recorded source
    # so a crafted body cannot pretend EventBridge was a person, and refuse
    # the synthetic demo catalog — that path exists for a laptop click, not
    # for a nightly production invocation.
    if actor.is_scheduler:
        if trigger.use_demo_catalog:
            raise HTTPException(400, "scheduled runs cannot use the demo catalog")
        trigger = trigger.model_copy(update={"source": "scheduled"})

    # Idempotency is checked before the lease, so a retry of a key that
    # already landed returns the original job rather than colliding with the
    # run it started and getting a 409. The check is not itself atomic —
    # two simultaneous retries can both miss here — which is why the unique
    # index is re-caught around `save_job` below.
    if trigger.idempotency_key:
        existing = state.repo.get_job_by_key(founder_id, trigger.idempotency_key)
        if existing is not None:
            response.status_code = 200
            return existing

    require_paid_work_capacity(state)
    enforce_rate_limit(
        state,
        actor,
        founder_id,
        scope="run_trigger",
        limit=state.config.manual_runs_per_hour,
        window_seconds=3600,
    )

    lease = state.run_lock.acquire(
        founder_id=founder_id, run_kind=job_module.RUN_KIND
    )
    if not lease.acquired:
        running = next(
            (
                j
                for j in state.repo.list_jobs(founder_id, limit=5)
                if not j.terminal()
            ),
            None,
        )
        raise HTTPException(
            409,
            {
                "detail": f"a run is already in progress for {founder_id}",
                "running_job_id": running.job_id if running else None,
            },
        )

    job = job_module.new_job(
        founder_id=founder_id,
        idempotency_key=trigger.idempotency_key,
        source=trigger.source,
        use_demo_catalog=trigger.use_demo_catalog,
        include_grants_gov=trigger.include_grants_gov,
    )
    try:
        state.repo.save_job(job)
    except Exception:
        # The unique index on the idempotency key fired: a concurrent
        # duplicate beat us to the insert. Return its job, not a second run.
        lease.release()
        if trigger.idempotency_key:
            existing = state.repo.get_job_by_key(
                founder_id, trigger.idempotency_key
            )
            if existing is not None:
                response.status_code = 200
                return existing
        raise

    # Ownership of the lease passes to the executor here: from this line on,
    # releasing it is `execute_job`'s `finally`, not this function's.
    state.executor.submit(job, lease)
    audit_event(
        actor=actor.subject,
        action="run.trigger",
        resource=job.job_id,
        method=actor.method,
        founder_id=founder_id,
        source=trigger.source,
    )
    return job


@router.get("/founders/{founder_id}/jobs")
def list_jobs(
    request: Request,
    founder_id: ResourceId,
    limit: ListLimit = 20,
    actor: Principal = Depends(principal),
) -> list[RunJob]:
    """Recent jobs for one founder, newest first — running and finished alike."""
    state = request.app.state
    owned(founder_id, actor)
    return state.repo.list_jobs(founder_id, limit)


@router.get("/founders/{founder_id}/jobs/{job_id}")
def get_job(
    request: Request,
    founder_id: ResourceId, job_id: ResourceId, actor: Principal = Depends(principal)
) -> dict:
    """One job, with its report once the run has one.

    The poll target for the dashboard's manual-run button. `report` is null
    until the run finishes; a halted run has a report too — halting is a
    reported outcome, not an error.
    """
    state = request.app.state
    owned(founder_id, actor, not_found=f"no job {job_id} for {founder_id}")
    job = state.repo.get_job(job_id)
    if job is None or job.founder_id != founder_id:
        raise HTTPException(404, f"no job {job_id} for {founder_id}")
    report = state.repo.get_run(job.run_id) if job.run_id else None
    return {"job": job, "report": report}


@router.post("/founders/{founder_id}/jobs/{job_id}/cancel")
def cancel_job(
    request: Request,
    founder_id: ResourceId, job_id: ResourceId, actor: Principal = Depends(principal)
) -> dict:
    """Ask the executor to stop a running job.

    Cooperative: the run stops at its next await point and the job records
    `cancelled`. What the run already persisted stays persisted — cancel
    stops future work, it does not rewrite history. A job that is already
    terminal, or running in a process this API cannot reach, reports
    `cancelled: false`.
    """
    state = request.app.state
    owned(
        founder_id,
        actor,
        write=True,
        scope=SCOPE_RUN_CANCEL,
        not_found=f"no job {job_id} for {founder_id}",
    )
    limit_authenticated_write(state, actor, founder_id)
    job = state.repo.get_job(job_id)
    if job is None or job.founder_id != founder_id:
        raise HTTPException(404, f"no job {job_id} for {founder_id}")
    if job.terminal():
        return {"cancelled": False, "status": job.status}
    cancelled = state.executor.cancel(job_id)
    # `job.status` was read before the cancel and is not re-read: a
    # successful cancel still reports `"running"` here, because the task
    # writes `cancelled` asynchronously when it reaches its next await
    # point. Callers should treat the `cancelled` flag as the answer and
    # poll `GET .../jobs/{job_id}` for the settled status.
    audit_event(
        actor=actor.subject,
        action="run.cancel",
        resource=job_id,
        outcome="ok" if cancelled else "not_running_here",
        method=actor.method,
    )
    return {"cancelled": cancelled, "status": job.status}


@router.get("/founders/{founder_id}/scheduler/failures")
def scheduler_failures(
    request: Request,
    founder_id: ResourceId,
    limit: ListLimit = 20,
    actor: Principal = Depends(principal),
) -> list:
    """Recent invocations that failed to start or finish, newest first.

    Sanitised before persistence — no credentials, no prompts, no stack
    traces. CloudWatch keeps the archive; this answers "did last night's
    run fail?" from the dashboard.
    """
    state = request.app.state
    owned(founder_id, actor)
    return state.failure_log.recent(founder_id, limit=limit)
