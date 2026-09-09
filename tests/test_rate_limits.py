"""Persistent rate counters are atomic and correctly scoped."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from api.repository import SqliteRepository


def test_limit_allows_exactly_the_configured_number_under_concurrency(tmp_path):
    repo = SqliteRepository(f"sqlite:///{tmp_path}/limits.db")
    now = datetime(2026, 9, 7, 12, 10, tzinfo=timezone.utc)

    def take(_: int) -> int | None:
        return repo.take_rate_limit(
            "manual_run",
            "user-1",
            "founder-1",
            limit=3,
            window_seconds=3600,
            now=now,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(take, range(8)))

    assert outcomes.count(None) == 3
    assert all(value is None or value == 3000 for value in outcomes)


def test_limits_are_separate_across_founders_and_principals(tmp_path):
    repo = SqliteRepository(f"sqlite:///{tmp_path}/scopes.db")
    now = datetime(2026, 9, 7, 12, 10, tzinfo=timezone.utc)

    def take(principal: str, founder: str) -> int | None:
        return repo.take_rate_limit(
            "write", principal, founder, limit=1, window_seconds=60, now=now
        )

    assert take("user-1", "founder-1") is None
    assert take("user-1", "founder-1") == 60
    assert take("user-1", "founder-2") is None
    assert take("user-2", "founder-1") is None


def test_a_new_window_restores_capacity(tmp_path):
    repo = SqliteRepository(f"sqlite:///{tmp_path}/expiry.db")
    now = datetime(2026, 9, 7, 12, 0, 30, tzinfo=timezone.utc)

    assert repo.take_rate_limit(
        "write", "user-1", "founder-1", limit=1, window_seconds=60, now=now
    ) is None
    assert repo.take_rate_limit(
        "write", "user-1", "founder-1", limit=1, window_seconds=60, now=now
    ) == 30
    assert repo.take_rate_limit(
        "write",
        "user-1",
        "founder-1",
        limit=1,
        window_seconds=60,
        now=now + timedelta(seconds=30),
    ) is None
