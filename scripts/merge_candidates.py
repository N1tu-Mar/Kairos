"""Fold researched rows into `data/opportunities.candidates.json`.

Curation produces batch files: one JSON array per research sweep, each row
carrying a `source_url`, structured eligibility, and `criteria[]` quotes
copied off the page. This script merges them into the candidate catalog that
`scripts/verify_seed.py` then verifies.

What it will not do:

*   **Promote anything.** It writes the *candidate* file. Every row still has
    to survive `verify_seed.py`, which re-fetches the page and re-finds every
    quote, before it can appear in `opportunities.seed.json` as verified.
*   **Invent a field.** Rows are validated against the real `Opportunity`
    model with `extra="forbid"`, so a typo or an invented key fails loudly
    rather than being silently carried into the catalog.
*   **Merge a row marked unreachable.** A researcher who could not read the
    page recorded that fact; turning it into a catalog row would be exactly
    the "plausible-sounding grant that doesn't exist" the rules forbid. Those
    rows are reported and skipped.

Deduplication is by `id` and by normalised `source_url`, first file wins, and
every collapse is printed — a silent dedup hides a disagreement between two
sources about the same program.

    uv run python scripts/merge_candidates.py <batch.json> [<batch.json> ...]
    uv run python scripts/merge_candidates.py --dry-run <batch.json>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.models import Opportunity  # noqa: E402

CANDIDATES = REPO_ROOT / "data" / "opportunities.candidates.json"

#: Written by curation and by the verifier, not part of the model.
_CURATION_KEYS = ("verified", "verified_at", "verification_note", "unreachable")


def normalize_url(url: str) -> str:
    """Canonical form of a source URL, for duplicate detection.

    Two rows pointing at the same page must collapse even when one has a
    trailing slash or a different scheme, because the point of the dedupe is
    to catch two researchers finding the same program.
    """
    return re.sub(r"/+$", "", (url or "").strip().lower()).replace("https://", "http://")


def validate(row: dict) -> tuple[bool, str]:
    """Would the runtime loader accept this row?

    Validated against the real `Opportunity` model rather than a bespoke
    schema, so the catalog cannot drift away from what Scout can load.
    """
    payload = {
        k: v for k, v in row.items() if not k.startswith("_") and k not in _CURATION_KEYS
    }
    payload.setdefault("source", "seed")
    try:
        Opportunity.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — the message is the product here
        return False, f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
    return True, ""


def stale_note(row: dict, today: date) -> str | None:
    """A deadline already in the past is reported, never quietly dropped."""
    raw = row.get("deadline")
    if not raw:
        return None
    try:
        deadline = date.fromisoformat(str(raw))
    except ValueError:
        return None
    if deadline >= today:
        return None
    return f"deadline {deadline.isoformat()} passed {(today - deadline).days} days ago"


def merge(batches: list[Path], existing: list[dict], today: date) -> tuple[list[dict], dict]:
    """Fold batch files into the existing rows and report every decision.

    First file wins on a collision, and every skip is categorised —
    unreachable, invalid, duplicate, stale — so the caller can print them.
    Nothing is dropped silently: a row that does not make it into the output
    appears in exactly one of those lists.

    Validation is against the real `Opportunity` model, so an invented key
    fails here rather than reaching the catalog.
    """
    rows = list(existing)
    by_id = {r.get("id"): r for r in rows}
    by_url = {normalize_url(r.get("source_url", "")): r for r in rows if r.get("source_url")}

    report = {
        "added": [],
        "skipped_unreachable": [],
        "skipped_duplicate": [],
        "skipped_invalid": [],
        "stale": [],
    }

    for batch in batches:
        for row in json.loads(Path(batch).read_text()):
            row_id = row.get("id", "")
            if row.get("unreachable"):
                report["skipped_unreachable"].append(
                    (row_id, row.get("_curation_note", "")[:120])
                )
                continue

            url_key = normalize_url(row.get("source_url", ""))
            if row_id in by_id:
                report["skipped_duplicate"].append((row_id, "same id already present"))
                continue
            if url_key and url_key in by_url:
                report["skipped_duplicate"].append(
                    (row_id, f"same source_url as {by_url[url_key].get('id')}")
                )
                continue

            clean = {k: v for k, v in row.items() if k != "unreachable"}
            ok, why = validate(clean)
            if not ok:
                report["skipped_invalid"].append((row_id, why))
                continue

            stale = stale_note(clean, today)
            if stale:
                report["stale"].append((row_id, stale))
                clean["_curation_note"] = (
                    f"{clean.get('_curation_note', '')} [stale] {stale}."
                ).strip()

            rows.append(clean)
            by_id[row_id] = clean
            if url_key:
                by_url[url_key] = clean
            report["added"].append(row_id)

    return rows, report


def main() -> int:
    """CLI entry. Writes the candidate file unless `--dry-run`. Always returns 0.

    What it writes is unverified by construction — `verify_seed.py` is what
    re-fetches each page and re-finds each quote, and only that can mark a
    row verified.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batches", nargs="+", type=Path)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    args = parser.parse_args()

    existing = json.loads(args.candidates.read_text()) if args.candidates.exists() else []
    rows, report = merge(args.batches, existing, args.today or date.today())

    for kind in ("skipped_unreachable", "skipped_invalid", "skipped_duplicate", "stale"):
        for row_id, detail in report[kind]:
            print(f"  {kind:22} {row_id}: {detail}")
    print(
        f"\n{len(report['added'])} added, "
        f"{len(report['skipped_duplicate'])} duplicate, "
        f"{len(report['skipped_invalid'])} invalid, "
        f"{len(report['skipped_unreachable'])} unreachable-skipped, "
        f"{len(report['stale'])} carrying a stale deadline. "
        f"Catalog is now {len(rows)} row(s)."
    )

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    args.candidates.write_text(json.dumps(rows, indent=2) + "\n")
    print(f"wrote {args.candidates}")
    print("Nothing is verified yet. Run: uv run python scripts/verify_seed.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
