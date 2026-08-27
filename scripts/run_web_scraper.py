"""Run the search-backed opportunity scraper.

Requires a search API key. The first supported provider is Brave Search:

    $env:BRAVE_SEARCH_API_KEY="..."
    python scripts/run_web_scraper.py --query "student founder grant"

Output is a candidate review file, never production seed data.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.scraping.agent import (  # noqa: E402
    DEFAULT_QUERIES,
    DEFAULT_WEB_CANDIDATES_PATH,
    BraveSearchClient,
    SearchApiError,
    WebScraperAgent,
    WebScraperConfig,
)
from agent.scraping.pipeline import RAW_DIR  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        action="append",
        help="search query to run; repeat for multiple queries",
    )
    parser.add_argument(
        "--domain",
        action="append",
        help="optional domain allowlist, e.g. rutgers.edu; repeat for more",
    )
    parser.add_argument("--max-results-per-query", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=25)
    parser.add_argument("--allow-js", action="store_true")
    parser.add_argument(
        "--include-weak-results",
        action="store_true",
        help="fetch search results even when title/snippet lacks funding words",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_WEB_CANDIDATES_PATH)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        search = BraveSearchClient.from_env()
    except SearchApiError as exc:
        print(exc, file=sys.stderr)
        print(
            "Set BRAVE_SEARCH_API_KEY, or KAIROS_SEARCH_API_KEY if you want a "
            "provider-neutral env name.",
            file=sys.stderr,
        )
        return 2

    config = WebScraperConfig(
        queries=tuple(args.query or DEFAULT_QUERIES),
        domains=tuple(args.domain or ()),
        max_results_per_query=args.max_results_per_query,
        max_pages=args.max_pages,
        allow_js=args.allow_js,
        require_opportunity_hint=not args.include_weak_results,
        raw_dir=args.raw_dir,
    )
    agent = WebScraperAgent(search_client=search, config=config)
    path, records, run = agent.write(path=args.out)

    print()
    print(run.headline())
    for record in records:
        award = (
            f"${record.award_min:,}-${record.award_max:,}"
            if record.award_min is not None and record.award_max is not None
            else "award UNKNOWN"
        )
        print(f"  - {record.title} - {award}, {len(record.unknown_fields)} UNKNOWN")
    for failure in run.failures:
        print(f"  ! {failure.url} - {failure.failure}")
    for note in run.notes:
        print(f"  - {note}")
    print()
    print(f"wrote {len(records)} candidate row(s) -> {path}")
    print("NOT written to data/opportunities.seed.json. Every row needs a human.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
