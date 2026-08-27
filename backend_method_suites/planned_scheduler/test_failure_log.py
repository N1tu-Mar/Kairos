"""`agent.scheduler.ScheduledRunFailureLog` — persistence, scoping, redaction."""

from __future__ import annotations

from agent.scheduler import FAILURE_HISTORY_LIMIT, ScheduledRunFailureLog


def test_record_and_latest_round_trip(tmp_path):
    log = ScheduledRunFailureLog(tmp_path / "failures.jsonl")
    log.record(
        founder_id="founder_demo",
        detail="worker timed out",
        source="scheduled",
        retry_count=1,
        failure_class="timeout",
    )

    latest = log.latest("founder_demo")
    assert latest is not None
    assert latest.detail == "worker timed out"
    assert latest.source == "scheduled"
    assert latest.retry_count == 1
    assert latest.failure_class == "timeout"
    assert latest.at  # timestamp present


def test_persists_across_instances(tmp_path):
    path = tmp_path / "failures.jsonl"
    ScheduledRunFailureLog(path).record(founder_id="founder_demo", detail="boom")

    reopened = ScheduledRunFailureLog(path)
    assert reopened.latest("founder_demo") is not None


def test_scoped_by_founder(tmp_path):
    log = ScheduledRunFailureLog(tmp_path / "failures.jsonl")
    log.record(founder_id="founder_a", detail="a failed")
    log.record(founder_id="founder_b", detail="b failed")

    assert log.latest("founder_a").detail == "a failed"
    assert log.latest("founder_b").detail == "b failed"
    assert [f.founder_id for f in log.recent("founder_a")] == ["founder_a"]


def test_empty_log_returns_nothing(tmp_path):
    log = ScheduledRunFailureLog(tmp_path / "never_written.jsonl")
    assert log.latest("founder_demo") is None
    assert log.recent("founder_demo") == []


def test_newest_first_and_limited(tmp_path):
    log = ScheduledRunFailureLog(tmp_path / "failures.jsonl")
    for index in range(5):
        log.record(founder_id="founder_demo", detail=f"failure {index}")

    recent = log.recent("founder_demo", limit=3)
    assert [f.detail for f in recent] == ["failure 4", "failure 3", "failure 2"]


def test_bearer_tokens_never_persist(tmp_path):
    """An exception message with a credential in it must not reach disk."""
    path = tmp_path / "failures.jsonl"
    log = ScheduledRunFailureLog(path)
    log.record(
        founder_id="founder_demo",
        detail="401 calling backend with Authorization: Bearer sk-live-abc123secret",
    )

    raw = path.read_text()
    assert "abc123secret" not in raw
    assert "[REDACTED]" in raw


def test_detail_is_capped(tmp_path):
    log = ScheduledRunFailureLog(tmp_path / "failures.jsonl")
    log.record(founder_id="founder_demo", detail="x" * 5000)
    assert len(log.latest("founder_demo").detail) <= 500


def test_history_is_bounded(tmp_path):
    path = tmp_path / "failures.jsonl"
    log = ScheduledRunFailureLog(path, limit=10)
    for index in range(25):
        log.record(founder_id="founder_demo", detail=f"failure {index}")

    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 10
    assert log.latest("founder_demo").detail == "failure 24"


def test_torn_line_loses_one_entry_not_the_file(tmp_path):
    path = tmp_path / "failures.jsonl"
    log = ScheduledRunFailureLog(path)
    log.record(founder_id="founder_demo", detail="good entry")
    with path.open("a") as handle:
        handle.write('{"founder_id": "founder_demo", "at": trunca\n')

    assert log.latest("founder_demo").detail == "good entry"


def test_default_limit_matches_constant(tmp_path):
    assert ScheduledRunFailureLog(tmp_path / "f.jsonl").limit == FAILURE_HISTORY_LIMIT
