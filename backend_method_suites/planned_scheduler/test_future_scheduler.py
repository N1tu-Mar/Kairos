"""The two behaviours this suite was created to pin, now implemented.

These were `xfail` while `agent.scheduler` did not exist. They stay exactly
as written — the ground truth did not move, the implementation caught up.
The full behaviour is exercised in `test_run_lease.py` and
`test_failure_log.py` alongside this file.
"""

from __future__ import annotations


def test_scheduler_lock_prevents_overlapping_founder_runs(tmp_path):
    from agent.scheduler import RunLock

    lock = RunLock(tmp_path / "locks")

    first = lock.acquire(founder_id="founder_demo", run_kind="daily")
    second = lock.acquire(founder_id="founder_demo", run_kind="daily")

    assert first.acquired is True
    assert second.acquired is False
    first.release()


def test_scheduler_records_failed_invocation_for_api_visibility(tmp_path):
    from agent.scheduler import ScheduledRunFailureLog

    log = ScheduledRunFailureLog(tmp_path / "scheduler_failures.jsonl")
    log.record(founder_id="founder_demo", detail="worker timed out")

    latest = log.latest("founder_demo")

    assert latest is not None
    assert latest.detail == "worker timed out"
