"""Re-check curated rows against their pages, and report what moved.

A `verified_at` timestamp is a claim about the past. Deadlines roll, award
amounts change, eligibility gets rewritten, and programs quietly end — so a
row verified in August is not a verified row in December, and the catalog has
no way to know that without asking.

This script asks. What it does **not** do is act on the answer:

    stale rows -> refetch -> compare -> data/reverification.report.json
                                          │
                                          └─> a human reads it and edits

Nothing is overwritten. Not the award, not the deadline, not `verified`. A
script that silently rewrites a curated fact because a page changed has
replaced human curation with a parser, which is the failure this whole
repository is arranged against. The report is the output; the edit is a
person's decision.

What it detects, per row:

*   **DEAD** — the page no longer resolves, or returns an error status.
*   **REDIRECTED** — the final URL differs from the stored one.
*   **EVIDENCE_LOST** — a `criteria[]` quote is no longer on the page it
    cites. Either the page was rewritten or the row was wrong.
*   **DEADLINE_PASSED** — the stored deadline is now in the past.
*   **TITLE_GONE** — the page no longer mentions the program.
*   **UNCHANGED** — everything still checks out; `verified_at` is refreshed
    in the report, for a human to copy across.

    uv run python scripts/reverify.py                  # rows older than 30 days
    uv run python scripts/reverify.py --max-age-days 7
    uv run python scripts/reverify.py --all
    uv run python scripts/reverify.py --offline-fixture tests/fixtures/reverify/
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_seed import (  # noqa: E402
    UA,
    evidence_pages,
    missing_evidence,
    normalize,
    page_mentions,
)

SEED = REPO_ROOT / "data" / "opportunities.seed.json"
REPORT = REPO_ROOT / "data" / "reverification.report.json"

DEFAULT_MAX_AGE_DAYS = 30


class Fetcher:
    """The one place this script touches the network.

    An offline fetcher reads a directory of recorded pages instead, which is
    what the tests use — the comparison logic is identical either way.
    """

    def __init__(self, timeout_s: float = 20.0, fixture_dir: Path | None = None):
        self.timeout_s = timeout_s
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self._cache: dict[str, tuple[int, str, str]] = {}

    def get(self, url: str) -> tuple[int, str, str]:
        """`(status, final_url, text)`. Status 0 means the fetch failed."""
        if url in self._cache:
            return self._cache[url]
        result = self._offline(url) if self.fixture_dir else self._online(url)
        self._cache[url] = result
        return result

    def _offline(self, url: str) -> tuple[int, str, str]:
        index = json.loads((self.fixture_dir / "index.json").read_text())
        entry = index.get(url)
        if entry is None:
            return 0, url, ""
        body = ""
        if entry.get("body_file"):
            body = (self.fixture_dir / entry["body_file"]).read_text()
        return entry.get("status", 200), entry.get("final_url", url), body

    def _online(self, url: str) -> tuple[int, str, str]:  # pragma: no cover - network
        import httpx

        try:
            response = httpx.get(
                url, timeout=self.timeout_s, follow_redirects=True,
                headers={"User-Agent": UA},
            )
        except Exception:  # noqa: BLE001 — a dead page is data, not a crash
            return 0, url, ""
        return response.status_code, str(response.url), response.text


def is_stale(row: dict, max_age_days: int, today: date) -> bool:
    """A row with no `verified_at` is stale by definition."""
    raw = row.get("verified_at")
    if not raw:
        return True
    try:
        checked = datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return True
    return checked < today - timedelta(days=max_age_days)


def _same_page(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


def check(row: dict, fetcher: Fetcher, today: date) -> dict:
    """One row's verdict. Pure comparison — nothing is written back."""
    url = row.get("source_url", "")
    finding = {
        "id": row.get("id", "?"),
        "title": row.get("title", ""),
        "source_url": url,
        "verified_at": row.get("verified_at"),
        "changes": [],
        "status": "UNCHANGED",
    }

    if not url:
        finding["status"] = "DEAD"
        finding["changes"].append({"field": "source_url", "detail": "row has no source_url"})
        return finding

    status, final_url, body = fetcher.get(url)

    if status == 0 or status >= 400:
        finding["status"] = "DEAD"
        finding["changes"].append(
            {
                "field": "availability",
                "was": "reachable at curation time",
                "now": f"HTTP {status}" if status else "fetch failed",
                "detail": "the program page no longer resolves; the row may need retiring",
            }
        )
        return finding

    if not _same_page(final_url, url):
        finding["status"] = "REDIRECTED"
        finding["changes"].append(
            {
                "field": "source_url",
                "was": url,
                "now": final_url,
                "detail": "the stored URL redirects; confirm it still describes this program",
            }
        )

    if not page_mentions(body, row.get("title", "")):
        finding["status"] = "TITLE_GONE"
        finding["changes"].append(
            {
                "field": "title",
                "was": row.get("title", ""),
                "now": "not mentioned on the page",
                "detail": "the page may have been retired into a generic landing page",
            }
        )
        return finding

    wanted, refused = evidence_pages(row)
    pages = {url: body}
    for cited in wanted:
        if _same_page(cited, url):
            continue
        cited_status, _, cited_body = fetcher.get(cited)
        if cited_status == 200:
            pages[cited] = cited_body
    for cited in refused:
        finding["changes"].append(
            {
                "field": "evidence",
                "detail": f"evidence cites {cited}, outside the funder's own site",
            }
        )

    lost = missing_evidence(pages, row)
    if lost:
        finding["status"] = "EVIDENCE_LOST"
        for quote in lost:
            finding["changes"].append(
                {
                    "field": "criteria",
                    "was": quote[:200],
                    "now": "not found on the page it cites",
                    "detail": "the page was rewritten, or this quote was never right. "
                              "Re-read the page before trusting the award, deadline or "
                              "eligibility this row carries.",
                }
            )

    deadline = row.get("deadline")
    if deadline:
        try:
            parsed = date.fromisoformat(str(deadline))
        except ValueError:
            parsed = None
        if parsed and parsed < today:
            if finding["status"] == "UNCHANGED":
                finding["status"] = "DEADLINE_PASSED"
            finding["changes"].append(
                {
                    "field": "deadline",
                    "was": parsed.isoformat(),
                    "now": f"passed {(today - parsed).days} days ago",
                    "detail": "the stored deadline is in the past; the next cycle may "
                              "have different dates, awards and rules",
                }
            )

    # A number that no longer appears anywhere on the page is worth a human's
    # eye. Reported as a hint, never as a correction.
    page_text = normalize(body)
    for field in ("award_min", "award_max"):
        value = row.get(field)
        if isinstance(value, int) and value and f"{value:,}" not in body and str(value) not in page_text:
            finding["changes"].append(
                {
                    "field": field,
                    "was": value,
                    "now": "figure not found on the page",
                    "detail": "the amount may have changed, or may live on a sub-page. "
                              "Confirm before relying on it.",
                }
            )

    return finding


def reverify(
    rows: list[dict], fetcher: Fetcher, *, max_age_days: int, today: date, check_all: bool
) -> dict:
    findings = []
    skipped_fresh = 0
    for row in rows:
        if not check_all and not is_stale(row, max_age_days, today):
            skipped_fresh += 1
            continue
        findings.append(check(row, fetcher, today))

    by_status: dict[str, int] = {}
    for finding in findings:
        by_status[finding["status"]] = by_status.get(finding["status"], 0) + 1

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "rows_in_catalog": len(rows),
        "rows_checked": len(findings),
        "rows_still_fresh": skipped_fresh,
        "max_age_days": None if check_all else max_age_days,
        "by_status": by_status,
        "needs_review": [f for f in findings if f["status"] != "UNCHANGED"],
        "unchanged": [f["id"] for f in findings if f["status"] == "UNCHANGED"],
        "note": (
            "Nothing in the catalog was modified. Every entry under needs_review "
            "is a question for a person: open the page, decide, and edit "
            "data/opportunities.candidates.json by hand. Re-running "
            "scripts/verify_seed.py afterwards is what updates verified_at."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=SEED)
    parser.add_argument("--out", type=Path, default=REPORT)
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--all", action="store_true", help="check every row, fresh or not")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--offline-fixture", type=Path, default=None)
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    args = parser.parse_args()

    rows = json.loads(args.seed.read_text())
    report = reverify(
        rows,
        Fetcher(args.timeout, args.offline_fixture),
        max_age_days=args.max_age_days,
        today=args.today or datetime.now(tz=timezone.utc).date(),
        check_all=args.all,
    )

    args.out.write_text(json.dumps(report, indent=2) + "\n")

    print(
        f"checked {report['rows_checked']} of {report['rows_in_catalog']} row(s); "
        f"{report['rows_still_fresh']} still within the freshness window"
    )
    for status, count in sorted(report["by_status"].items()):
        print(f"  {status:16} {count}")
    for finding in report["needs_review"]:
        print(f"\n  {finding['status']}: {finding['id']}")
        for change in finding["changes"]:
            print(f"    - {change['field']}: {change.get('detail', '')}")
    print(f"\nwrote {args.out}")
    print("Nothing was changed. Curated facts are edited by people, not by this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
