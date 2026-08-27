"""The async job boundary: POST /runs answers fast, the job row tells the truth.

Success, idempotent replay, overlap conflict, crash recovery, timeout,
cancellation, and malformed requests — each is a way the old
hold-the-socket-open design failed silently, pinned here as behaviour.
"""

from __future__ import annotations

import time

import pytest

from agent.models import RunJob
from agent.scheduler import ScheduledRunFailureLog
from api.repository import SqliteRepository


def _wait_terminal(api_client, founder_id: str, job_id: str, timeout_s: float = 10.0) -> dict:
    """Poll the status endpoint the way the dashboard does."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = api_client.get(f"/founders/{founder_id}/jobs/{job_id}").json()
        if body["job"]["status"] in ("succeeded", "halted", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached a terminal state")


def _trigger(api_client, **overrides):
    body = {"use_demo_catalog": True, "include_grants_gov": False, "source": "manual"}
    body.update(overrides)
    return api_client.post("/founders/founder_demo/runs", json=body)


# ── Success ──────────────────────────────────────────────────────────────────


def test_post_returns_202_with_a_job_not_a_report(api_client):
    response = _trigger(api_client)
    assert response.status_code == 202
    job = response.json()
    assert job["job_id"].startswith("job_")
    assert job["status"] in ("queued", "running")
    assert job["run_id"] is None

    final = _wait_terminal(api_client, "founder_demo", job["job_id"])
    assert final["job"]["status"] in ("succeeded", "halted")
    assert final["report"] is not None
    assert final["report"]["run_id"] == final["job"]["run_id"]


def test_job_appears_in_the_job_list(api_client):
    job_id = _trigger(api_client).json()["job_id"]
    _wait_terminal(api_client, "founder_demo", job_id)

    listed = api_client.get("/founders/founder_demo/jobs").json()
    assert job_id in [j["job_id"] for j in listed]


# ── Idempotency ──────────────────────────────────────────────────────────────


def test_same_idempotency_key_resolves_to_the_same_job(api_client):
    first = _trigger(api_client, idempotency_key="sched-exec-1")
    assert first.status_code == 202
    _wait_terminal(api_client, "founder_demo", first.json()["job_id"])

    retry = _trigger(api_client, idempotency_key="sched-exec-1")
    assert retry.status_code == 200
    assert retry.json()["job_id"] == first.json()["job_id"]


def test_different_keys_create_different_jobs(api_client):
    first = _trigger(api_client, idempotency_key="key-a")
    _wait_terminal(api_client, "founder_demo", first.json()["job_id"])
    second = _trigger(api_client, idempotency_key="key-b")
    assert second.status_code == 202
    assert second.json()["job_id"] != first.json()["job_id"]
    _wait_terminal(api_client, "founder_demo", second.json()["job_id"])


# ── Conflict ─────────────────────────────────────────────────────────────────


def test_overlapping_run_is_a_409_naming_the_running_job(api_client):
    from api.main import app

    lease = app.state.run_lock.acquire(founder_id="founder_demo", run_kind="pipeline")
    assert lease.acquired
    try:
        response = _trigger(api_client)
        assert response.status_code == 409
        assert "already in progress" in str(response.json())
    finally:
        lease.release()


def test_conflict_does_not_burn_the_idempotency_key(api_client):
    """A 409'd request must be retryable with the same key once the lease frees."""
    from api.main import app

    lease = app.state.run_lock.acquire(founder_id="founder_demo", run_kind="pipeline")
    try:
        assert _trigger(api_client, idempotency_key="after-conflict").status_code == 409
    finally:
        lease.release()

    retry = _trigger(api_client, idempotency_key="after-conflict")
    assert retry.status_code == 202
    _wait_terminal(api_client, "founder_demo", retry.json()["job_id"])


def test_lease_is_released_after_the_run_finishes(api_client):
    job_id = _trigger(api_client).json()["job_id"]
    _wait_terminal(api_client, "founder_demo", job_id)

    again = _trigger(api_client)
    assert again.status_code == 202
    _wait_terminal(api_client, "founder_demo", again.json()["job_id"])


# ── Malformed requests ───────────────────────────────────────────────────────


def test_unknown_founder_is_a_404(api_client):
    response = api_client.post("/founders/nobody/runs", json={"source": "manual"})
    assert response.status_code == 404


def test_non_json_body_is_a_422(api_client):
    response = api_client.post(
        "/founders/founder_demo/runs",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422


def test_job_ids_are_founder_scoped(api_client):
    job_id = _trigger(api_client).json()["job_id"]
    _wait_terminal(api_client, "founder_demo", job_id)
    assert api_client.get(f"/founders/somebody_else/jobs/{job_id}").status_code == 404


# ── Crash recovery ───────────────────────────────────────────────────────────


def test_startup_recovery_fails_jobs_no_process_is_running(tmp_path):
    """A row that says 'running' with no process behind it becomes 'failed'."""
    from api.jobs import recover_orphaned_jobs
    from agent.scheduler import RunLock

    repo = SqliteRepository(f"sqlite:///{tmp_path}/crash.db")
    repo.save_job(
        RunJob(job_id="job_orphan", founder_id="founder_demo", status="running")
    )
    failure_log = ScheduledRunFailureLog(tmp_path / "failures.jsonl")

    recovered = recover_orphaned_jobs(
        repo, failure_log, RunLock(tmp_path / "locks")
    )

    assert recovered == 1
    repaired = repo.get_job("job_orphan")
    assert repaired.status == "failed"
    assert "restarted" in repaired.error
    latest = failure_log.latest("founder_demo")
    assert latest is not None
    assert latest.failure_class == "orphaned"


def test_startup_recovery_leaves_terminal_jobs_alone(tmp_path):
    from api.jobs import recover_orphaned_jobs
    from agent.scheduler import RunLock

    repo = SqliteRepository(f"sqlite:///{tmp_path}/crash.db")
    repo.save_job(
        RunJob(job_id="job_done", founder_id="founder_demo", status="succeeded")
    )
    recovered = recover_orphaned_jobs(
        repo,
        ScheduledRunFailureLog(tmp_path / "failures.jsonl"),
        RunLock(tmp_path / "locks"),
    )
    assert recovered == 0
    assert repo.get_job("job_done").status == "succeeded"


# ── Run-start failure lands in the failure log ───────────────────────────────


def test_start_failure_is_recorded_and_releases_the_lease(tmp_path):
    """SubAgents that cannot build = a startup failure class, not a hang."""
    import asyncio

    from agent.scheduler import RunLock
    from api.jobs import execute_job, new_job

    class ExplodingRepo(SqliteRepository):
        def get_profile(self, founder_id):
            raise RuntimeError("database on fire, Authorization: Bearer sk-secret99")

    repo = ExplodingRepo(f"sqlite:///{tmp_path}/start.db")
    lock = RunLock(tmp_path / "locks")
    lease = lock.acquire(founder_id="founder_demo", run_kind="pipeline")
    failure_log = ScheduledRunFailureLog(tmp_path / "failures.jsonl")
    job = new_job(
        founder_id="founder_demo",
        idempotency_key=None,
        source="scheduled",
        use_demo_catalog=True,
        include_grants_gov=False,
    )

    asyncio.run(execute_job(job, repo, lease, failure_log))

    stored = repo.get_job(job.job_id)
    assert stored.status == "failed"
    latest = failure_log.latest("founder_demo")
    assert latest.failure_class == "startup"
    assert latest.source == "scheduled"
    assert "sk-secret99" not in latest.detail
    # The lease came back: the next invocation is not locked out.
    assert lock.acquire(founder_id="founder_demo", run_kind="pipeline").acquired


# ── Timeout ──────────────────────────────────────────────────────────────────


def test_run_that_outlives_the_timeout_fails_with_the_timeout_class(
    tmp_path, monkeypatch
):
    import asyncio

    from agent import config
    from agent.scheduler import RunLock
    from api import jobs as job_module
    from api.jobs import execute_job, new_job

    monkeypatch.setenv("KAIROS_RUN_TIMEOUT_S", "0.05")
    monkeypatch.setenv("KAIROS_DB_URL", f"sqlite:///{tmp_path}/timeout.db")
    config.settings.cache_clear()

    async def hang_forever(ctx, sources):
        await asyncio.sleep(3600)

    monkeypatch.setattr(job_module, "run_once", hang_forever)

    repo = SqliteRepository(f"sqlite:///{tmp_path}/timeout.db")
    from tests.factories import profile

    repo.save_profile(profile(founder_id="founder_demo"))
    lock = RunLock(tmp_path / "locks")
    lease = lock.acquire(founder_id="founder_demo", run_kind="pipeline")
    failure_log = ScheduledRunFailureLog(tmp_path / "failures.jsonl")
    job = new_job(
        founder_id="founder_demo",
        idempotency_key=None,
        source="manual",
        use_demo_catalog=True,
        include_grants_gov=False,
    )

    asyncio.run(execute_job(job, repo, lease, failure_log))

    assert repo.get_job(job.job_id).status == "failed"
    assert failure_log.latest("founder_demo").failure_class == "timeout"
    assert lock.acquire(founder_id="founder_demo", run_kind="pipeline").acquired


# ── Cancellation ─────────────────────────────────────────────────────────────


def test_cancel_of_a_terminal_job_is_a_no_op(api_client):
    job_id = _trigger(api_client).json()["job_id"]
    _wait_terminal(api_client, "founder_demo", job_id)

    response = api_client.post(f"/founders/founder_demo/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["cancelled"] is False


def test_cancel_of_an_unknown_job_is_a_404(api_client):
    assert (
        api_client.post("/founders/founder_demo/jobs/job_missing/cancel").status_code
        == 404
    )


# ── Scheduler failure visibility ─────────────────────────────────────────────


def test_failure_endpoint_is_empty_before_any_failure(api_client):
    assert api_client.get("/founders/founder_demo/scheduler/failures").json() == []


def test_failure_endpoint_is_founder_scoped_and_sanitised(api_client):
    from api.main import app

    app.state.failure_log.record(
        founder_id="founder_demo",
        detail="run died holding Authorization: Bearer sk-live-topsecret",
        source="scheduled",
        failure_class="crash",
    )
    app.state.failure_log.record(founder_id="founder_other", detail="other broke")

    body = api_client.get("/founders/founder_demo/scheduler/failures").json()
    assert len(body) == 1
    assert body[0]["failure_class"] == "crash"
    assert "topsecret" not in body[0]["detail"]

    # Another founder's failures are not reachable at all: in local mode the
    # principal owns founder_demo and nothing else, and a founder it does not
    # own is a 404 rather than a 403.
    assert (
        api_client.get("/founders/founder_other/scheduler/failures").status_code
        == 404
    )
