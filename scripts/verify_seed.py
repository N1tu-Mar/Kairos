"""Verify seed catalog candidates against their live source URLs.

Section 0.5 rule 3: *never write seed data from memory.* Every row in
`data/opportunities.seed.json` needs a `source_url` someone actually fetched
and a `verified_at` timestamp. A row that fails verification gets
`"verified": false` and is excluded from runs.

This script is what makes that rule mechanical instead of aspirational. It
reads `data/opportunities.candidates.json`, fetches every `source_url`, and
writes `data/opportunities.seed.json` with an honest verdict on each row.

It verifies two things, and is honest about the difference:

1.  **Reachability** — the page exists and still mentions the program title.
    A 200 alone does not prove a manually entered value is correct.
2.  **Evidence presence** — every verbatim quote the row carries in
    `criteria[].text` actually appears on the fetched page (whitespace- and
    punctuation-normalised). A row whose quoted evidence cannot be found on
    its own source page fails verification. This is the mechanical guard
    against fabricated or drifted evidence: a curator (human or agent) cannot
    invent a supporting quote without the verifier catching it.

It still does not verify *interpretation* — that `award_max: 10000` is the
right reading of the quoted sentence is a human judgment. What it removes is
the failure class where the quote itself never existed.

    uv run python scripts/verify_seed.py
    uv run python scripts/verify_seed.py --strict   # exit 1 if any row fails
"""

from __future__ import annotations

import argparse
import html as html_mod
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


def normalize(text: str) -> str:
    """Lowercase, tags stripped, every run of non-alphanumerics collapsed to
    one space. Makes quote matching robust to markup, curly quotes and
    whitespace without weakening it to keyword search."""
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def page_mentions(html: str, phrase: str) -> bool:
    """Does the fetched page actually talk about this program?"""
    text = re.sub(r"<[^>]+>", " ", html).lower()
    tokens = [t for t in re.findall(r"[a-z]{4,}", phrase.lower())]
    if not tokens:
        return True
    hits = sum(1 for t in tokens if t in text)
    return hits >= max(1, len(tokens) // 2)


def missing_evidence(html: str, row: dict) -> list[str]:
    """Every `criteria[].text` quote that does NOT appear on the page.

    Quotes are matched normalised, as substrings. An empty return means every
    quoted evidence span was found verbatim (modulo whitespace/punctuation).
    """
    page = normalize(html)
    missing = []
    for criterion in row.get("criteria") or []:
        quote = criterion.get("text", "")
        if quote and normalize(quote) not in page:
            missing.append(quote)
    return missing


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

    lost = missing_evidence(response.text, row)
    if lost:
        result["verified"] = False
        result["verified_at"] = None
        result["verification_note"] = (
            f"page returned 200 but {len(lost)} quoted evidence span(s) were "
            f"not found on it — the quote may be fabricated, or the page has "
            f"changed since curation. First missing: {lost[0][:120]!r}"
        )
        return result

    checked = len(row.get("criteria") or [])
    result["verified"] = True
    result["verified_at"] = datetime.now(timezone.utc).isoformat()
    result["verification_note"] = (
        f"HTTP 200, title terms present, {checked} evidence quote(s) found on "
        f"page ({len(response.text)} bytes)"
    )
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
