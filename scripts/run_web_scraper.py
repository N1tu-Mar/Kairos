"""Run search-backed opportunity scrapers.

Requires one search API key. The first supported provider is Brave Search:

    $env:BRAVE_SEARCH_API_KEY="..."
    python scripts/run_web_scraper.py --lane university
    python scripts/run_web_scraper.py --lane general
    python scripts/run_web_scraper.py --lane both

Output is candidate review data, never production seed data.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.scraping.agent import (  # noqa: E402
    GENERAL_LANE,
    LANES,
    UNIVERSITY_LANE,
    BraveSearchClient,
    GeneralWebScraperAgent,
    ScraperLane,
    SearchApiError,
    SearchClient,
    UniversityWebScraperAgent,
    WebScraperAgent,
    WebScraperConfig,
    lane_by_name,
)
from agent.scraping.pipeline import RAW_DIR  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lane",
        choices=[*sorted(LANES), "both"],
        default="general",
        help="which scraper lane to run",
    )
    parser.add_argument(
        "--query",
        action="append",
        help="search query to run; repeat for multiple queries; overrides lane defaults",
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
    parser.add_argument(
        "--out",
        type=Path,
        help="candidate output file; only valid when running one lane",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="directory for lane-named output files; useful with --lane both",
    )
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def lanes_for(value: str) -> tuple[ScraperLane, ...]:
    if value == "both":
        return (UNIVERSITY_LANE, GENERAL_LANE)
    return (lane_by_name(value),)


def config_for_lane(args: argparse.Namespace, lane: ScraperLane) -> WebScraperConfig:
    return WebScraperConfig.for_lane(
        lane,
        queries=tuple(args.query or lane.queries),
        domains=tuple(args.domain or lane.domains),
        max_results_per_query=args.max_results_per_query,
        max_pages=args.max_pages,
        allow_js=args.allow_js,
        require_opportunity_hint=not args.include_weak_results,
        raw_dir=args.raw_dir,
    )


def output_path_for(args: argparse.Namespace, lane: ScraperLane) -> Path:
    if args.out is not None:
        return args.out
    if args.out_dir is not None:
        return args.out_dir / lane.output_path.name
    return lane.output_path


def agent_for_lane(
    lane: ScraperLane,
    search: SearchClient,
    config: WebScraperConfig,
    fetcher=None,
) -> WebScraperAgent:
    if lane.name == UNIVERSITY_LANE.name:
        return UniversityWebScraperAgent(search, config=config, fetcher=fetcher)
    if lane.name == GENERAL_LANE.name:
        return GeneralWebScraperAgent(search, config=config, fetcher=fetcher)
    return WebScraperAgent(search_client=search, config=config, fetcher=fetcher)


def print_summary(lane: ScraperLane, path: Path, records, run) -> None:
    print()
    print(f"[{lane.name}] {run.headline()}")
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
    print(f"wrote {len(records)} {lane.name} candidate row(s) -> {path}")


def main(argv: list[str] | None = None, *, search_client=None, fetcher=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    lanes = lanes_for(args.lane)
    if len(lanes) > 1 and args.out is not None:
        print(
            "--out can only be used with one lane; use --out-dir with --lane both.",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if search_client is None:
        try:
            search_client = BraveSearchClient.from_env()
        except SearchApiError as exc:
            print(exc, file=sys.stderr)
            print(
                "Set BRAVE_SEARCH_API_KEY, or KAIROS_SEARCH_API_KEY if you want a "
                "provider-neutral env name.",
                file=sys.stderr,
            )
            return 2

    for lane in lanes:
        config = config_for_lane(args, lane)
        agent = agent_for_lane(lane, search_client, config, fetcher=fetcher)
        path, records, run = agent.write(path=output_path_for(args, lane))
        print_summary(lane, path, records, run)

    print("NOT written to data/opportunities.seed.json. Every row needs a human.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
