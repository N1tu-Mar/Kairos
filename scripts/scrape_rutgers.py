"""Scrape Rutgers-relevant student funding opportunities into a review file.

    uv run python scripts/scrape_rutgers.py                 # the full target list
    uv run python scripts/scrape_rutgers.py --priority 1    # priority 1 only
    uv run python scripts/scrape_rutgers.py --discover      # follow Rutgers-domain links
    uv run python scripts/scrape_rutgers.py --allow-js      # render the one JS page
    uv run python scripts/scrape_rutgers.py --doc           # rewrite the review doc

Output goes to `data/opportunities.rutgers.candidates.json` and never to
`data/opportunities.seed.json`. Raw HTML and the robots.txt each decision was
made against are archived under `data/raw/`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.scraping.pipeline import (  # noqa: E402
    CANDIDATES_PATH,
    RAW_DIR,
    scrape,
    write_candidates,
)
from agent.scraping.registry import TARGETS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--priority", type=int, default=2, help="fetch targets at or above")
    parser.add_argument("--key", action="append", help="fetch only these registry keys")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="follow funding-shaped links, Rutgers domains only, one level deep",
    )
    parser.add_argument(
        "--allow-js",
        action="store_true",
        help="render pages already proven to be JavaScript shells (needs playwright)",
    )
    parser.add_argument("--out", type=Path, default=CANDIDATES_PATH)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--doc", action="store_true", help="also write the review document")
    parser.add_argument(
        "--doc-out", type=Path, default=Path("docs/rutgers-funding-review.md")
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    targets = [t for t in TARGETS if t.priority <= args.priority]
    if args.key:
        targets = [t for t in targets if t.key in set(args.key)]
    if not targets:
        print("no targets matched", file=sys.stderr)
        return 1

    records, run = scrape(
        targets,
        raw_dir=args.raw_dir,
        allow_js=args.allow_js,
        discover=args.discover,
    )
    path = write_candidates(records, run, path=args.out)

    print()
    print(run.headline())
    for record in records:
        unknown = len(record.unknown_fields)
        award = (
            f"${record.award_min:,}-${record.award_max:,}"
            if record.award_min is not None and record.award_max is not None
            else "award UNKNOWN"
        )
        print(f"  · {record.title} — {award}, {unknown} field(s) UNKNOWN")
    for failure in run.failures:
        print(f"  ! {failure.url} — {failure.failure}")
    for note in run.notes:
        print(f"  · {note}")
    print()
    print(f"wrote {len(records)} candidate row(s) -> {path}")
    print("NOT written to data/opportunities.seed.json. Every row needs a human.")

    if args.doc:
        from agent.scraping.render import write_review_doc

        doc = write_review_doc(records, run, args.doc_out)
        print(f"wrote review document -> {doc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
