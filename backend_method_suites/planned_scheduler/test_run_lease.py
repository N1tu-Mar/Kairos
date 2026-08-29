"""`agent.scheduler.RunLock` — the overlap lock, exercised hard.

The lease has to hold across threads, across processes, across a process
restart, and through exceptions. Each of those is a separate test because
each is a separate way the demo-era "runs never overlap" assumption breaks
in production.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from agent.scheduler import RunLock

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_second_acquire_is_refused(tmp_path):
    lock = RunLock(tmp_path / "locks")
    first = lock.acquire(founder_id="founder_demo", run_kind="daily")
    second = lock.acquire(founder_id="founder_demo", run_kind="daily")

    assert first.acquired is True
    assert second.acquired is False
    # The refusal says who holds it and until when, so the API can report it.
    assert second.held_until is not None


def test_release_frees_the_lease(tmp_path):
    lock = RunLock(tmp_path / "locks")
    first = lock.acquire(founder_id="founder_demo", run_kind="daily")
    assert first.release() is True

    second = lock.acquire(founder_id="founder_demo", run_kind="daily")
    assert second.acquired is True


def test_different_keys_do_not_contend(tmp_path):
    lock = RunLock(tmp_path / "locks")
    a = lock.acquire(founder_id="founder_a", run_kind="daily")
    b = lock.acquire(founder_id="founder_b", run_kind="daily")
    c = lock.acquire(founder_id="founder_a", run_kind="manual")

    assert a.acquired and b.acquired and c.acquired


def test_simultaneous_acquisition_from_many_threads(tmp_path):
    """Exactly one winner, no matter how many race."""
    lock_dir = tmp_path / "locks"
    results = []
    barrier = threading.Barrier(8)

    def attempt():
        """Try to take the lease, from this thread's own RunLock over the same directory.

        A separate `RunLock` per thread on purpose: the guarantee under
        test is cross-process, enforced by SQLite, not by shared in-memory
        state.
        """
        lock = RunLock(lock_dir)
        barrier.wait()
        results.append(lock.acquire(founder_id="founder_demo", run_kind="daily"))

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r.acquired]
    assert len(winners) == 1


def test_acquisition_holds_across_processes(tmp_path):
    """A lease taken here is visible to a completely separate process."""
    lock_dir = tmp_path / "locks"
    lock = RunLock(lock_dir)
    held = lock.acquire(founder_id="founder_demo", run_kind="daily")
    assert held.acquired

    probe = (
        "from agent.scheduler import RunLock; import sys; "
        f"lease = RunLock({str(lock_dir)!r}).acquire("
        "founder_id='founder_demo', run_kind='daily'); "
        "sys.exit(0 if not lease.acquired else 1)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr

    held.release()
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 1, "released lease should be acquirable elsewhere"


def test_stale_lease_is_taken_over(tmp_path):
    """A crashed holder's lease expires and the next acquirer recovers it."""
    lock = RunLock(tmp_path / "locks", ttl_seconds=0)
    abandoned = lock.acquire(founder_id="founder_demo", run_kind="daily")
    assert abandoned.acquired

    takeover = RunLock(tmp_path / "locks").acquire(
        founder_id="founder_demo", run_kind="daily"
    )
    assert takeover.acquired is True


def test_wrong_owner_cannot_release(tmp_path):
    """The stale first holder cannot yank the lease from its successor."""
    lock = RunLock(tmp_path / "locks", ttl_seconds=0)
    stale = lock.acquire(founder_id="founder_demo", run_kind="daily")
    fresh = RunLock(tmp_path / "locks", ttl_seconds=3600).acquire(
        founder_id="founder_demo", run_kind="daily"
    )
    assert fresh.acquired

    # The stale holder's token no longer owns the row.
    assert stale.release() is False

    # The fresh lease is untouched: a third acquire is still refused.
    third = lock.acquire(founder_id="founder_demo", run_kind="daily")
    assert third.acquired is False

    assert fresh.release() is True


def test_lease_survives_process_restart(tmp_path):
    """A new RunLock over the same directory sees the existing lease.

    This is the restart scenario: the API process that acquired the lease
    dies, a replacement starts, and until the TTL passes the replacement must
    still see the run as in-flight.
    """
    RunLock(tmp_path / "locks").acquire(founder_id="founder_demo", run_kind="daily")

    restarted = RunLock(tmp_path / "locks")
    assert restarted.acquire(founder_id="founder_demo", run_kind="daily").acquired is False
    holder = restarted.holder(founder_id="founder_demo", run_kind="daily")
    assert holder is not None
    assert holder.token is None, "holder() must never expose the ownership token"


def test_exception_inside_context_manager_releases(tmp_path):
    lock = RunLock(tmp_path / "locks")
    try:
        with lock.acquire(founder_id="founder_demo", run_kind="daily") as lease:
            assert lease.acquired
            raise RuntimeError("run blew up")
    except RuntimeError:
        pass

    assert lock.acquire(founder_id="founder_demo", run_kind="daily").acquired is True


def test_double_release_is_harmless(tmp_path):
    lock = RunLock(tmp_path / "locks")
    lease = lock.acquire(founder_id="founder_demo", run_kind="daily")
    assert lease.release() is True
    assert lease.release() is False

    # And it cannot delete a lease someone else took in the meantime.
    other = lock.acquire(founder_id="founder_demo", run_kind="daily")
    assert lease.release() is False
    assert lock.acquire(founder_id="founder_demo", run_kind="daily").acquired is False
    other.release()
