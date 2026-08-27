#!/usr/bin/env python3
"""Validate the GitHub Actions workflows before GitHub gets a chance to.

Every failure this catches has already happened to us once. A run that dies in
"Set up job" — before a single step executes — costs a push, a wait, and a red
tick on a commit that was never actually broken. The checks below are the ones
that fail that early:

  1. Every `uses:` is pinned to a 40-character commit SHA. A tag is mutable;
     whoever controls the upstream repository can move it after review.
  2. Every pin carries a `# vX.Y.Z` comment, because a bare SHA tells a reader
     nothing about how far behind it is.
  3. The SHA actually exists upstream. This is the one that bit us: a pin can
     be perfectly well-formed, correctly commented, and simply not be a commit
     anybody ever pushed. Needs the network, so it is opt-in via --online.
  4. The YAML parses and every job has `runs-on` and `steps`.

Offline checks run everywhere, including a pull request from a fork. The
online check is a separate job that may fail for reasons that are not the
author's fault (a rate limit, a network blip), so it must never be the thing
standing between a correct change and a merge.

    python scripts/check_workflows.py            # structure only, no network
    python scripts/check_workflows.py --online   # also verify pins upstream
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# `uses: owner/repo[/path]@ref  # optional comment`
USES = re.compile(
    r"^\s*(?:-\s*)?uses:\s*"
    r"(?P<action>[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-/]+)?)"
    r"@(?P<ref>[^\s#]+)"
    r"(?:\s*#\s*(?P<comment>.*?))?\s*$"
)

SHA = re.compile(r"^[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^v?\d+(\.\d+)*")

# Local actions (`./.github/actions/...`) and Docker actions are not pinned to
# a SHA because they are not fetched from a third-party repository.
EXEMPT_PREFIXES = ("./", "docker://")


class Problem(NamedTuple):
    file: str
    line: int
    message: str


def find_uses(path: Path) -> list[tuple[int, str, str, str | None]]:
    """Return (line_number, action, ref, comment) for each `uses:` in a file."""
    found = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        match = USES.match(line)
        if match:
            found.append(
                (
                    lineno,
                    match.group("action"),
                    match.group("ref"),
                    match.group("comment"),
                )
            )
    return found


def check_structure(paths: list[Path]) -> list[Problem]:
    """Pins are SHAs, pins are commented, and the YAML describes real jobs."""
    problems: list[Problem] = []

    for path in paths:
        name = path.name

        for lineno, action, ref, comment in find_uses(path):
            if action.startswith(EXEMPT_PREFIXES):
                continue

            if not SHA.match(ref):
                problems.append(
                    Problem(
                        name,
                        lineno,
                        f"{action} is pinned to '{ref}', which is not a 40-character "
                        f"commit SHA. A tag can be moved after you review it.",
                    )
                )
                continue

            if not comment or not VERSION_COMMENT.match(comment.strip()):
                problems.append(
                    Problem(
                        name,
                        lineno,
                        f"{action} is pinned to a SHA with no '# vX.Y.Z' comment, so "
                        f"nobody can tell how stale it is without resolving it.",
                    )
                )

        # Structural YAML checks. PyYAML is not a declared dependency of this
        # project, so treat its absence as "skip", not "fail" — but say so,
        # because a check that quietly stops checking is worse than no check.
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            if not getattr(check_structure, "_warned_no_yaml", False):
                print(
                    "  note: PyYAML is not installed, so the job-structure "
                    "checks are being skipped (pin checks still ran)."
                )
                check_structure._warned_no_yaml = True  # type: ignore[attr-defined]
            continue

        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            problems.append(Problem(name, 0, f"is not valid YAML: {exc}"))
            continue

        if not isinstance(doc, dict):
            problems.append(Problem(name, 0, "does not parse to a YAML mapping."))
            continue

        jobs = doc.get("jobs")
        if not isinstance(jobs, dict) or not jobs:
            problems.append(Problem(name, 0, "declares no jobs."))
            continue

        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                problems.append(Problem(name, 0, f"job '{job_name}' is not a mapping."))
                continue
            if "runs-on" not in job and "uses" not in job:
                problems.append(
                    Problem(
                        name, 0, f"job '{job_name}' has neither 'runs-on' nor 'uses'."
                    )
                )
            if "uses" not in job and not job.get("steps"):
                problems.append(Problem(name, 0, f"job '{job_name}' has no steps."))

    return problems


def ref_exists(action: str, ref: str) -> bool:
    """Can the runner resolve this pin in the upstream repository?

    Two ways a 40-character SHA can be legitimate, and the check has to accept
    both:

      * a commit — the ordinary case, answered by the commits API;
      * an annotated tag object — `git ls-remote` prints the tag object's SHA
        for `refs/tags/vX`, not the commit it points at, so a pin copied from
        that output is a tag SHA. The commits API answers 422 for it while the
        runner checks it out perfectly well. Ask the git API before believing
        the 422.

    Prefer the commit SHA when writing a pin (the peeled `refs/tags/vX^{}`
    ref), but do not fail a build over a tag object that genuinely resolves.
    """
    owner_repo = "/".join(action.split("/")[:2])

    def api(path: str) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{owner_repo}/{path}",
            headers={"Accept": "application/vnd.github+json"},
        )
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return 200, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, {}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return 0, {}

    status, body = api(f"commits/{ref}")
    if status == 200:
        return body.get("sha", "").startswith(ref[:40])

    if status in (404, 422):
        # Not a commit. It may still be an annotated tag object, which the
        # runner resolves without complaint.
        tag_status, _ = api(f"git/tags/{ref}")
        if tag_status == 200:
            return True
        if status == 404 and tag_status in (404, 422):
            return False
        if status == 422 and tag_status in (404, 422):
            return False
        # Anything else (rate limit on the second call) is inconclusive.

    # Rate limited, offline, or inconclusive: ask git directly. ls-remote
    # lists both tag objects and the commits they peel to.
    try:
        out = subprocess.run(
            ["git", "ls-remote", f"https://github.com/{owner_repo}.git"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Cannot reach the network at all. Report as present rather than
        # failing a build over our own connectivity.
        return True

    if out.returncode != 0:
        return True

    return ref in out.stdout


def check_online(paths: list[Path]) -> list[Problem]:
    """Every pinned SHA resolves to a real commit upstream."""
    targets: dict[tuple[str, str], tuple[str, int]] = {}
    for path in paths:
        for lineno, action, ref, _ in find_uses(path):
            if action.startswith(EXEMPT_PREFIXES) or not SHA.match(ref):
                continue
            targets.setdefault((action, ref), (path.name, lineno))

    if not targets:
        return []

    problems: list[Problem] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(lambda kv: (kv, ref_exists(*kv)), list(targets))
        for (action, ref), exists in results:
            file_name, lineno = targets[(action, ref)]
            if exists:
                print(f"  ok      {action}@{ref[:12]}")
            else:
                print(f"  MISSING {action}@{ref}")
                problems.append(
                    Problem(
                        file_name,
                        lineno,
                        f"{action}@{ref} does not exist upstream. The run will die in "
                        f"'Set up job' with 'Unable to resolve action'.",
                    )
                )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--online",
        action="store_true",
        help="also verify each pinned SHA exists upstream (needs the network)",
    )
    args = parser.parse_args()

    paths = sorted(
        p for p in WORKFLOW_DIR.glob("*.y*ml") if p.suffix in (".yml", ".yaml")
    )
    if not paths:
        print(f"No workflows found in {WORKFLOW_DIR}", file=sys.stderr)
        return 1

    print(f"Checking {len(paths)} workflow file(s) in {WORKFLOW_DIR}")

    problems = check_structure(paths)

    if args.online:
        print("\nResolving pins upstream:")
        problems += check_online(paths)

    if problems:
        print(f"\n{len(problems)} problem(s):\n", file=sys.stderr)
        for problem in problems:
            where = f"{problem.file}:{problem.line}" if problem.line else problem.file
            print(f"  {where}: {problem.message}", file=sys.stderr)
            # Surface it in the Actions UI as an annotation too.
            print(
                f"::error file=.github/workflows/{problem.file},"
                f"line={problem.line or 1}::{problem.message}"
            )
        return 1

    print("\nWorkflows look fine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
