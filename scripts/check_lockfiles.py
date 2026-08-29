#!/usr/bin/env python3
"""Check that the committed lockfiles install on the machine CI actually uses.

The failure this exists to prevent: `npm install` on macOS writes a
package-lock.json with the linux-only optional dependencies pruned, because npm
resolves optional platform packages for the host it is running on. `npm ci` on
ubuntu-latest then demands the entries that were never written and refuses to
install anything:

    npm error `npm ci` can only install packages when your package.json and
    package-lock.json are in sync.
    npm error Missing: @emnapi/runtime@1.11.3 from lock file
    npm error Missing: @emnapi/core@1.11.3 from lock file

Nothing about that is visible locally — `npm ci` on the laptop that wrote the
lockfile passes. The check therefore re-resolves the tree for linux/x64 in a
scratch directory and compares it against what is committed, which is the same
comparison the runner makes.

    python scripts/check_lockfiles.py          # report drift, exit 1
    python scripts/check_lockfiles.py --fix    # rewrite the lockfile in place

--fix is what you want after touching frontend/package.json. Run it, commit the
lockfile it produces, and CI installs exactly that tree.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

# What ubuntu-latest is. Resolving for this pair is the entire point: the
# lockfile has to satisfy `npm ci` there, not on whatever laptop wrote it.
CI_OS = "linux"
CI_CPU = "x64"


def resolve_for_ci(package_json: Path, workdir: Path) -> dict:
    """Resolve the dependency tree as ubuntu-latest would, in a clean room.

    A clean room matters: npm reads an existing node_modules when deciding
    which optional dependencies are reachable, so re-resolving next to a
    macOS-populated node_modules reproduces the macOS answer and the drift
    stays invisible. Copy the manifest somewhere empty instead.
    """
    shutil.copy(package_json, workdir / "package.json")

    result = subprocess.run(
        [
            "npm",
            "install",
            "--package-lock-only",
            "--no-audit",
            "--no-fund",
            f"--os={CI_OS}",
            f"--cpu={CI_CPU}",
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"npm install failed:\n{result.stderr[-2000:]}")

    return json.loads((workdir / "package-lock.json").read_text())


def compare(committed: dict, expected: dict) -> tuple[list[str], list[str]]:
    """Packages CI would need that are absent, and ones we carry needlessly."""
    have = set(committed.get("packages", {}))
    want = set(expected.get("packages", {}))
    return sorted(want - have), sorted(have - want)


def main() -> int:
    """CLI entry. 0 when the lockfile matches CI's resolution, 1 when it drifts.

    Exits 0 and skips the check when `npm` is not on PATH — a missing
    toolchain is not evidence of drift, and failing here would break every
    Python-only contributor's preflight.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="rewrite the lockfile with the CI-resolved tree",
    )
    args = parser.parse_args()

    package_json = FRONTEND / "package.json"
    lockfile = FRONTEND / "package-lock.json"

    if not package_json.is_file():
        print(f"No package.json at {package_json}", file=sys.stderr)
        return 1

    if shutil.which("npm") is None:
        print("npm is not on PATH; skipping the lockfile check.")
        return 0

    print(f"Re-resolving frontend dependencies for {CI_OS}/{CI_CPU}...")

    with tempfile.TemporaryDirectory() as tmp:
        try:
            expected = resolve_for_ci(package_json, Path(tmp))
        except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            print(f"Could not resolve dependencies: {exc}", file=sys.stderr)
            return 1

        if args.fix:
            lockfile.write_text(json.dumps(expected, indent=2) + "\n")
            print(f"Wrote {lockfile.relative_to(ROOT)} "
                  f"({len(expected.get('packages', {}))} packages).")
            print("Commit it — `npm ci` on the runner installs exactly this tree.")
            return 0

        if not lockfile.is_file():
            print(f"::error::{lockfile.relative_to(ROOT)} is missing. "
                  f"Run: python scripts/check_lockfiles.py --fix")
            return 1

        committed = json.loads(lockfile.read_text())
        missing, extra = compare(committed, expected)

    if not missing:
        count = len(committed.get("packages", {}))
        note = f" ({len(extra)} host-only extras, harmless)" if extra else ""
        print(f"Lockfile satisfies a {CI_OS}/{CI_CPU} install: "
              f"{count} packages{note}.")
        return 0

    # Only `missing` fails. Extra entries are packages CI will not install but
    # that another developer's platform needs, and `npm ci` tolerates those.
    print(
        f"\n::error::The lockfile is missing {len(missing)} package(s) that "
        f"`npm ci` needs on {CI_OS}/{CI_CPU}. The Frontend job will fail at Install.",
        file=sys.stderr,
    )
    for name in missing[:20]:
        version = expected["packages"][name].get("version", "?")
        print(f"  missing: {name}@{version}", file=sys.stderr)
    if len(missing) > 20:
        print(f"  ... and {len(missing) - 20} more", file=sys.stderr)

    print("\nFix it with:\n  python scripts/check_lockfiles.py --fix", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
