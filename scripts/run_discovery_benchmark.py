"""Score the catalog against a hand-authored reference set. Offline.

Discovery has been unmeasured. "We find campus funding" is a claim with no
number behind it, and the number is not flattering, which is the reason to
publish it.

    uv run python scripts/run_discovery_benchmark.py
    uv run python scripts/run_discovery_benchmark.py --json

## What this measures

*   **Retrieval recall** — of the in-scope programs in the reference set, how
    many appear in the catalog at all.
*   **Source-level recall** — the same, split by where a program lives
    (campus page, national competition, government, fellowship), because a
    catalog can look healthy overall while missing an entire channel.
*   **Duplicates** — programs represented by more than one row.
*   **Stale and expired** — rows whose deadline has passed.
*   **Deadline extraction accuracy** — of the programs whose reference entry
    states a deadline, how many rows carry that exact date. A row that
    honestly says UNKNOWN is counted separately from one that says the wrong
    date, because they are different failures.
*   **Structured eligibility precision and coverage** — of the eligibility
    facts the reference set states, how many the catalog carries (coverage)
    and how many of those it gets right (precision).
*   **Form coverage** — how many scored programs have a transcribed
    application form.
*   **Negative handling** — deliberate negatives (equity-taking, non-cash,
    closed, defunct, indirect) that the catalog carries as verified rows.

## What it cannot claim

*   **It is not a measure of the whole funding universe.** The reference set
    is 20 programs a person chose. Recall against it is recall against that
    list, not against everything a student founder could apply for. Adding a
    program the catalog already has would raise the score without improving
    the product, which is exactly why the set is version-controlled and
    changes to it are reviewable.
*   **It says nothing about drafting groundedness.** That is the Section
    11.11 golden set, deliberately kept separate: one measures whether we
    found the money, the other whether we lied on the application.
*   **It cannot detect a program nobody thought of.** Recall against a
    reference set is a lower bound on ignorance, not a measure of it.
*   **Its ground truth ages.** Each entry carries `as_of`. A mismatch may
    mean the catalog is wrong or that the program changed; the benchmark
    reports the disagreement and does not adjudicate it.

Ground truth comes from the programs' own pages and is not derived from the
catalog, the scraper, or any code being scored here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

REFERENCE = REPO_ROOT / "tests" / "discovery_benchmark" / "reference_set.json"
SEED = REPO_ROOT / "data" / "opportunities.seed.json"
CAMPUS = REPO_ROOT / "data" / "opportunities.rutgers.candidates.json"
FORMS_DIR = REPO_ROOT / "data" / "forms"


def host(url: str) -> str:
    return urlsplit(url or "").netloc.lower().removeprefix("www.")


def site(url: str) -> str:
    """Last two labels of the host — `rbpc.rice.edu` and `rice.edu` match."""
    parts = host(url).split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else ""


def _distinctive(name: str, url: str) -> set[str]:
    """Title tokens that are not just the organisation's own name.

    "Alpha Innovation Fund" and "Alpha Dining Services Survey" both live on
    alpha.edu and share the token "alpha". Counting that as a match inflates
    recall with rows about entirely different things, so host-derived tokens
    are dropped before comparing.
    """
    host_tokens = {t for t in re.split(r"[^a-z0-9]+", host(url)) if len(t) > 3}
    return {
        t
        for t in re.split(r"[^a-z0-9]+", (name or "").lower())
        if len(t) > 3 and t not in host_tokens and t not in _GENERIC_TITLE_WORDS
    }


#: Words that appear in half the program names in this space and therefore
#: discriminate nothing on their own.
_GENERIC_TITLE_WORDS = frozenset(
    {"competition", "challenge", "program", "prize", "award", "awards", "fund",
     "grant", "grants", "student", "students", "university", "college", "annual"}
)


def matches(program: dict, row: dict) -> bool:
    """Does this catalog row represent this reference program?

    Matched on site plus a distinctive-token overlap rather than on id,
    because ids are ours and the benchmark must not be scoring our own
    naming. Where a program's name is *entirely* generic after filtering, the
    site match alone carries it.
    """
    row_site = site(row.get("source_url", ""))
    if not row_site or row_site != site(program["canonical_url"]):
        return False
    wanted = _distinctive(program["name"], program["canonical_url"])
    have = _distinctive(row.get("title") or "", row.get("source_url") or "")
    return bool(wanted & have) or not wanted


def _eligibility_checks(program: dict, row: dict) -> tuple[int, int, int]:
    """`(stated, carried, correct)` for one program's eligibility facts."""
    expected = program.get("expected_eligibility") or {}
    rules = row.get("eligibility") or {}
    stated = carried = correct = 0

    for key, value in expected.items():
        stated += 1
        if key == "degree_levels_include":
            actual = rules.get("degree_levels")
            if actual is not None:
                carried += 1
                if all(level in actual for level in value):
                    correct += 1
        else:
            actual = rules.get(key)
            if actual is not None:
                carried += 1
                if actual == value:
                    correct += 1
    return stated, carried, correct


def score(reference: dict, rows: list[dict], campus_rows: list[dict], forms: set[str], today: date) -> dict:
    programs = reference["programs"]
    in_scope = [p for p in programs if p["in_scope"]]
    negatives = [p for p in programs if not p["in_scope"]]

    found, missed, duplicates = [], [], []
    by_category: dict[str, dict[str, int]] = {}
    deadline_exact = deadline_unknown = deadline_wrong = deadline_stated = 0
    elig_stated = elig_carried = elig_correct = 0
    stale_rows, unverified_hits, with_form = [], [], []

    for program in in_scope:
        category = program["category"]
        bucket = by_category.setdefault(category, {"expected": 0, "found": 0})
        bucket["expected"] += 1

        hits = [r for r in rows if matches(program, r)]
        if not hits:
            # A campus row awaiting review is not a catalog hit, but it is a
            # different kind of miss and worth separating.
            awaiting = [
                c
                for c in campus_rows
                if site(c.get("source_url", "")) == site(program["canonical_url"])
                and c.get("review_status") != "ACCEPTED"
            ]
            missed.append(
                {
                    "key": program["key"],
                    "name": program["name"],
                    "category": category,
                    "reason": "awaiting human review in the campus candidates file"
                    if awaiting
                    else "not in the catalog",
                }
            )
            continue

        bucket["found"] += 1
        found.append(program["key"])
        if len(hits) > 1:
            duplicates.append({"key": program["key"], "rows": [h.get("id") for h in hits]})

        row = hits[0]
        if not row.get("verified"):
            unverified_hits.append({"key": program["key"], "row": row.get("id"),
                                    "note": row.get("verification_note", "")})
        if program["key"] in forms or row.get("id") in forms:
            with_form.append(program["key"])

        expected_deadline = program.get("expected_deadline")
        if expected_deadline:
            deadline_stated += 1
            actual = row.get("deadline")
            if actual == expected_deadline:
                deadline_exact += 1
            elif actual in (None, ""):
                deadline_unknown += 1
            else:
                deadline_wrong += 1

        row_deadline = row.get("deadline")
        if row_deadline:
            try:
                if date.fromisoformat(str(row_deadline)) < today:
                    stale_rows.append({"key": program["key"], "deadline": row_deadline})
            except ValueError:
                pass

        stated, carried, correct = _eligibility_checks(program, row)
        elig_stated += stated
        elig_carried += carried
        elig_correct += correct

    # Carrying a negative is not automatically wrong. An equity-taking
    # program in the catalog is *correct* as long as the row records
    # `takes_equity`, because that is what lets the deterministic filter drop
    # it for a non-dilutive founder with a readable reason. What is wrong is
    # carrying it with the disqualifier missing, where it reaches judgment
    # looking like a grant.
    negatives_marked, negatives_unmarked = [], []
    for program in negatives:
        hits = [r for r in rows if matches(program, r) and r.get("verified")]
        if not hits:
            continue
        row = hits[0]
        rules = row.get("eligibility") or {}
        expected = program.get("expected_eligibility") or {}
        entry = {
            "key": program["key"],
            "category": program["category"],
            "row": row.get("id"),
            "why_it_is_a_negative": program.get("note", ""),
        }
        marked = True
        if expected.get("takes_equity") is True and rules.get("takes_equity") is not True:
            marked = False
            entry["missing"] = "eligibility.takes_equity is not True on the row"
        (negatives_marked if marked else negatives_unmarked).append(entry)

    def pct(numerator: int, denominator: int) -> float:
        return round(100.0 * numerator / denominator, 1) if denominator else 0.0

    return {
        "reference_version": reference["version"],
        "generated_at": datetime.now().astimezone().isoformat(),
        "catalog_rows": len(rows),
        "reference_programs": len(programs),
        "in_scope_programs": len(in_scope),
        "deliberate_negatives": len(negatives),
        "retrieval_recall_pct": pct(len(found), len(in_scope)),
        "retrieved": len(found),
        "missed": missed,
        "source_recall": {
            category: {**counts, "recall_pct": pct(counts["found"], counts["expected"])}
            for category, counts in sorted(by_category.items())
        },
        "duplicates": duplicates,
        "stale_rows": stale_rows,
        "deadline_accuracy": {
            "programs_with_a_reference_deadline": deadline_stated,
            "exact": deadline_exact,
            "honestly_unknown": deadline_unknown,
            "wrong": deadline_wrong,
            "exact_pct": pct(deadline_exact, deadline_stated),
        },
        "structured_eligibility": {
            "facts_in_reference": elig_stated,
            "facts_carried": elig_carried,
            "facts_correct": elig_correct,
            "coverage_pct": pct(elig_carried, elig_stated),
            "precision_pct": pct(elig_correct, elig_carried),
        },
        "form_coverage": {
            "programs_with_a_form": len(with_form),
            "of_retrieved_pct": pct(len(with_form), len(found)),
            "programs": with_form,
        },
        "retrieved_but_unverified": unverified_hits,
        "negatives_carried_and_correctly_marked": negatives_marked,
        "negatives_carried_without_their_disqualifier": negatives_unmarked,
    }


def render(result: dict) -> str:
    lines = [
        f"Discovery benchmark — reference set {result['reference_version']}",
        "",
        f"  retrieval recall        {result['retrieval_recall_pct']}%  "
        f"({result['retrieved']}/{result['in_scope_programs']} in-scope programs)",
        f"  catalog rows            {result['catalog_rows']}",
        f"  duplicates              {len(result['duplicates'])}",
        f"  stale (deadline passed) {len(result['stale_rows'])}",
        f"  retrieved but unverified {len(result['retrieved_but_unverified'])}",
        "",
        "  source-level recall:",
    ]
    for category, counts in result["source_recall"].items():
        lines.append(
            f"    {category:24} {counts['recall_pct']:5}%  "
            f"({counts['found']}/{counts['expected']})"
        )
    deadline = result["deadline_accuracy"]
    eligibility = result["structured_eligibility"]
    lines += [
        "",
        f"  deadline extraction     {deadline['exact_pct']}% exact "
        f"({deadline['exact']} exact, {deadline['honestly_unknown']} honestly UNKNOWN, "
        f"{deadline['wrong']} wrong, of {deadline['programs_with_a_reference_deadline']})",
        f"  eligibility coverage    {eligibility['coverage_pct']}% "
        f"({eligibility['facts_carried']}/{eligibility['facts_in_reference']} facts carried)",
        f"  eligibility precision   {eligibility['precision_pct']}% "
        f"({eligibility['facts_correct']}/{eligibility['facts_carried']} carried facts correct)",
        f"  form coverage           {result['form_coverage']['of_retrieved_pct']}% of retrieved "
        f"({result['form_coverage']['programs_with_a_form']} programs)",
        "",
    ]
    if result["missed"]:
        lines.append("  missed:")
        for miss in result["missed"]:
            lines.append(f"    - {miss['name']} ({miss['category']}): {miss['reason']}")
    if result["negatives_carried_and_correctly_marked"]:
        lines.append(
            "  deliberate negatives carried WITH their disqualifier (correct — the "
            "filter can drop them with a reason):"
        )
        for negative in result["negatives_carried_and_correctly_marked"]:
            lines.append(f"    - {negative['key']} ({negative['category']}) as {negative['row']}")
    if result["negatives_carried_without_their_disqualifier"]:
        lines.append("  deliberate negatives carried WITHOUT their disqualifier (a real defect):")
        for negative in result["negatives_carried_without_their_disqualifier"]:
            lines.append(
                f"    - {negative['key']} as {negative['row']}: {negative.get('missing', '')}"
            )
    lines += [
        "",
        "  This measures retrieval against a hand-authored 20-program reference set.",
        "  It is not a measure of the funding universe, and it says nothing about",
        "  drafting groundedness — that is the Section 11.11 golden set.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    parser.add_argument("--seed", type=Path, default=SEED)
    parser.add_argument("--campus", type=Path, default=CAMPUS)
    parser.add_argument("--forms", type=Path, default=FORMS_DIR)
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    reference = json.loads(args.reference.read_text())
    rows = json.loads(args.seed.read_text()) if args.seed.exists() else []
    campus_rows = json.loads(args.campus.read_text()) if args.campus.exists() else []
    forms = {
        json.loads(p.read_text()).get("opportunity_id", "")
        for p in args.forms.glob("*.json")
    } if args.forms.exists() else set()

    result = score(reference, rows, campus_rows, forms, args.today or date.today())
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
