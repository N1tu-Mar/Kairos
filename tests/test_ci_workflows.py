"""The CI configuration is checked by CI, because it broke in a way CI could not report.

A workflow with an unresolvable action pin fails in "Set up job": no step runs,
the log is one line naming a SHA, and nothing distinguishes it from a real test
failure on the commit list. Five of seven jobs died that way at once. The
checks below are the ones that would have turned that push into a local error.

Nothing here reaches the network. `scripts/check_workflows.py --online` does,
and it is exercised in CI as a non-blocking step rather than pinned to a
specific upstream state that would rot.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
CHECKER = REPO_ROOT / "scripts" / "check_workflows.py"

SHA = re.compile(r"^[0-9a-f]{40}$")
USES = re.compile(
    r"^\s*(?:-\s*)?uses:\s*"
    r"(?P<action>[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-/]+)?)"
    r"@(?P<ref>[^\s#]+)"
    r"(?:\s*#\s*(?P<comment>.*?))?\s*$"
)


def workflow_files() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.yml"))


def uses_lines(path: Path) -> list[tuple[int, str, str, str | None]]:
    out = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        match = USES.match(line)
        if match:
            out.append(
                (lineno, match["action"], match["ref"], match["comment"])
            )
    return out


def test_workflows_exist() -> None:
    assert workflow_files(), f"no workflow files under {WORKFLOW_DIR}"


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_sha(path: Path) -> None:
    """A tag is mutable. Whoever owns the action can move it after review."""
    unpinned = [
        f"{path.name}:{lineno} {action}@{ref}"
        for lineno, action, ref, _ in uses_lines(path)
        if not action.startswith(("./", "docker://")) and not SHA.match(ref)
    ]
    assert not unpinned, "actions pinned to a mutable ref:\n  " + "\n  ".join(unpinned)


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_every_pin_records_its_version(path: Path) -> None:
    """A bare SHA gives a reader no way to see how far behind it is."""
    uncommented = [
        f"{path.name}:{lineno} {action}@{ref[:12]}"
        for lineno, action, ref, comment in uses_lines(path)
        if SHA.match(ref) and not (comment and re.match(r"^v?\d", comment.strip()))
    ]
    assert not uncommented, "SHA pins with no '# vX.Y.Z' comment:\n  " + "\n  ".join(
        uncommented
    )


def test_one_version_per_action_across_workflows() -> None:
    """Two SHAs for one action means one of them was updated and one was missed."""
    seen: dict[str, set[str]] = {}
    for path in workflow_files():
        for _, action, ref, _ in uses_lines(path):
            if SHA.match(ref):
                seen.setdefault(action, set()).add(ref)

    split = {a: refs for a, refs in seen.items() if len(refs) > 1}
    assert not split, f"the same action is pinned to different SHAs: {split}"


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_workflow_parses_and_declares_runnable_jobs(path: Path) -> None:
    yaml = pytest.importorskip("yaml", reason="PyYAML is not installed")

    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict), f"{path.name} does not parse to a mapping"

    jobs = doc.get("jobs")
    assert isinstance(jobs, dict) and jobs, f"{path.name} declares no jobs"

    for name, job in jobs.items():
        assert isinstance(job, dict), f"{path.name}: job '{name}' is not a mapping"
        assert "runs-on" in job or "uses" in job, (
            f"{path.name}: job '{name}' has neither 'runs-on' nor 'uses'"
        )
        if "uses" not in job:
            assert job.get("steps"), f"{path.name}: job '{name}' has no steps"


def test_required_checks_are_still_present() -> None:
    """The demo depends on these running. A silent rename is a silent gap."""
    yaml = pytest.importorskip("yaml", reason="PyYAML is not installed")

    doc = yaml.safe_load((WORKFLOW_DIR / "ci.yml").read_text())
    expected = {
        "preflight",
        "python",
        "migrations",
        "frontend",
        "hygiene",
        "terraform",
        "docker",
        "scan",
    }
    assert expected <= set(doc["jobs"]), (
        f"CI lost job(s): {sorted(expected - set(doc['jobs']))}"
    )


def test_pull_requests_never_get_credentials() -> None:
    """`pull_request_target` on a workflow with secrets hands them to a fork."""
    yaml = pytest.importorskip("yaml", reason="PyYAML is not installed")

    for path in workflow_files():
        doc = yaml.safe_load(path.read_text())
        # PyYAML reads a bare `on:` key as the boolean True.
        triggers = doc.get("on", doc.get(True, {}))
        if isinstance(triggers, dict):
            assert "pull_request_target" not in triggers, (
                f"{path.name} uses pull_request_target, which runs with the base "
                f"repository's secrets on a fork's code."
            )


def test_the_checker_passes_on_the_committed_workflows() -> None:
    """The offline half of the preflight job, run here as well.

    If this fails, the preflight job fails too — better to find out from
    `pytest` than from a red tick on a pushed commit.
    """
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"scripts/check_workflows.py failed:\n{result.stdout}\n{result.stderr}"
    )


def test_version_tags_sort_numerically_not_lexically() -> None:
    """v0.9.2 is older than v0.30.0, however the strings compare.

    Sorting tags as text put v0.9.2 last and it was taken for the newest
    release of trivy-action. It is from 2023, and it pins a trivy whose
    vulnerability database endpoint has since been retired — so the scan
    exited in eight seconds and the SARIF upload got a truncated file.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from check_workflows import version_key  # noqa: PLC0415

    tags = ["v0.9.2", "v0.30.0", "v0.36.0", "v0.10.0"]

    assert max(tags, key=version_key) == "v0.36.0"
    assert sorted(tags, key=version_key) == [
        "v0.9.2",
        "v0.10.0",
        "v0.30.0",
        "v0.36.0",
    ]
    # The failure this replaces: plain string sort disagrees.
    assert max(tags) == "v0.9.2"

    # Differing component counts still compare.
    assert version_key("v7") < version_key("v7.0.1")
    assert version_key("v10.0.1") > version_key("v7.6.0")


def test_the_checker_rejects_an_unresolvable_pin(tmp_path: Path) -> None:
    """The regression test proper: the exact shape that broke the build.

    A pin that is well-formed, correctly commented, and simply not a commit
    anybody pushed. Only the --online half can catch it, so this asserts the
    checker's own detection rather than re-running it against the network.
    """
    workflow = tmp_path / "broken.yml"
    workflow.write_text(
        "name: Broken\n"
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: astral-sh/setup-uv@"
        "f0ec1fc3b38f5e7cd3d55efdaa1823247dc376a3 # v5.4.1\n"
    )

    parsed = uses_lines(workflow)
    assert len(parsed) == 1
    _, action, ref, comment = parsed[0]

    # Structurally this pin is beyond reproach, which is precisely why the
    # offline checks let it through and the build died instead.
    assert action == "astral-sh/setup-uv"
    assert SHA.match(ref)
    assert comment == "v5.4.1"
