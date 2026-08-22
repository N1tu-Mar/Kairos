"""Verify seed catalog candidates against their live source URLs.

Section 0.5 rule 3: *never write seed data from memory.* Every row in
`data/opportunities.seed.json` needs a `source_url` someone actually fetched
and a `verified_at` timestamp. A row that fails verification gets
`"verified": false` and is excluded from runs.

This script is what makes that rule mechanical instead of aspirational. It
reads `data/opportunities.candidates.json`, fetches every `source_url`, and
writes `data/opportunities.seed.json` with an honest verdict on each row.

It verifies **reachability**, not correctness. A 200 means the page exists;
it does not mean the award range and eligibility on the row are right. A
human still has to read the page. What this prevents is the specific failure
that is easy to miss in review: a confidently-written row pointing at a URL
that has never existed.

    uv run python scripts/verify_seed.py
    uv run python scripts/verify_seed.py --strict   # exit 1 if any row fails

Optionally checks that a keyword from the title appears in the fetched page,
which catches a URL that resolves to a generic 200 landing page after the
real one was retired.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = REPO_ROOT / "data" / "opportunities.candidates.json"
SEED = REPO_ROOT / "data" / "opportunities.seed.json"

UA = "Mozilla/5.0 (compatible; kairos-seed-verifier/1.0)"


def page_mentions(html: str, phrase: str) -> bool:
    """Does the fetched page actually talk about this program?"""
    text = re.sub(r"<[^>]+>", " ", html).lower()
    tokens = [t for t in re.findall(r"[a-z]{4,}", phrase.lower())]
    if not tokens:
        return True
    hits = sum(1 for t in tokens if t in text)
    return hits >= max(1, len(tokens) // 2)


def verify(row: dict, timeout_s: float) -> dict:
    url = row.get("source_url", "")
    result = dict(row)

    if not url:
        result["verified"] = False
        result["verified_at"] = None
        result["verification_note"] = "no source_url on this row"
        return result

    try:
        response = httpx.get(
            url, timeout=timeout_s, follow_redirects=True, headers={"User-Agent": UA}
        )
    except httpx.HTTPError as exc:
        result["verified"] = False
        result["verified_at"] = None
        result["verification_note"] = f"fetch failed: {type(exc).__name__}: {exc}"
        return result

    if response.status_code != 200:
        result["verified"] = False
        result["verified_at"] = None
        result["verification_note"] = f"HTTP {response.status_code}"
        return result

    if not page_mentions(response.text, row.get("title", "")):
        result["verified"] = False
        result["verified_at"] = None
        result["verification_note"] = (
            "page returned 200 but does not mention the program title — "
            "the URL may have been retired into a generic landing page"
        )
        return result

    result["verified"] = True
    result["verified_at"] = datetime.now(timezone.utc).isoformat()
    result["verification_note"] = f"HTTP 200, title terms present ({len(response.text)} bytes)"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--out", type=Path, default=SEED)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--strict", action="store_true", help="exit non-zero if any row fails"
    )
    args = parser.parse_args()

    if not args.candidates.exists():
        print(f"no candidates file at {args.candidates}", file=sys.stderr)
        return 1

    rows = json.loads(args.candidates.read_text())
    verified = [verify(row, args.timeout) for row in rows]

    args.out.write_text(json.dumps(verified, indent=2) + "\n")

    passed = sum(1 for r in verified if r["verified"])
    print(f"{passed}/{len(verified)} rows verified -> {args.out}")
    for row in verified:
        if not row["verified"]:
            print(f"  FAIL {row.get('id', '?')}: {row['verification_note']}")

    return 1 if args.strict and passed != len(verified) else 0


if __name__ == "__main__":
    raise SystemExit(main())
