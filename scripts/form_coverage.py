"""Which catalog opportunities have a usable application form, and which do not.

Discovering a fund and judging it is not enough to draft its application. The
form has to exist as structured JSON with verbatim labels, or the Drafter has
nothing to fill. This prints that gap honestly, per row, so "we model real
forms" can never be said about a directory holding one synthetic example.

    uv run python scripts/form_coverage.py
    uv run python scripts/form_coverage.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.guardrails import blocklisted  # noqa: E402
from agent.models import ApplicationForm  # noqa: E402

SEED = REPO_ROOT / "data" / "opportunities.seed.json"
FORMS_DIR = REPO_ROOT / "data" / "forms"


def load_forms(directory: Path = FORMS_DIR) -> dict[str, ApplicationForm]:
    """Load every form JSON in `directory`, keyed by opportunity id.

    A later file with the same `opportunity_id` overwrites an earlier one, so
    the count printed by this script counts opportunities, not files.
    """
    forms = {}
    for path in sorted(directory.glob("*.json")):
        form = ApplicationForm.model_validate(json.loads(path.read_text()))
        forms[form.opportunity_id] = form
    return forms


def report(rows: list[dict], forms: dict[str, ApplicationForm]) -> dict:
    """Build the coverage report: which catalog rows have a form, and how complete each is.

    Also reports `forms_with_no_catalog_row` — a form transcribed for an
    opportunity that is no longer in the catalog, which is the direction of
    drift that otherwise goes unnoticed.

    Protected fields are recomputed from `blocklisted()` rather than read off
    the form's `protected` flag, so the report shows what the gate would
    actually refuse rather than what the curator marked.
    """
    real_forms = {k: f for k, f in forms.items() if not k.startswith("demo_")}
    verified_ids = {r["id"] for r in rows if r.get("verified")}

    with_form, without_form = [], []
    for row in rows:
        entry = {
            "id": row["id"],
            "title": row.get("title", ""),
            "verified": bool(row.get("verified")),
        }
        form = real_forms.get(row["id"])
        if form is None:
            without_form.append(entry)
            continue
        protected = [f for f in form.fields if f.protected or blocklisted(f.label)]
        with_form.append(
            {
                **entry,
                "form_source_url": form.source_url,
                "retrieved_at": str(form.retrieved_at) if form.retrieved_at else None,
                "fields": len(form.fields),
                "required_fields": sum(1 for f in form.fields if f.required),
                "protected_fields": [f.field_id for f in protected],
                "complete": form.complete,
                "completeness_note": form.completeness_note,
            }
        )

    orphans = [k for k in real_forms if k not in {r["id"] for r in rows}]

    return {
        "catalog_rows": len(rows),
        "verified_rows": len(verified_ids),
        "real_forms": len(real_forms),
        "synthetic_forms": len(forms) - len(real_forms),
        "rows_with_a_form": len(with_form),
        "rows_with_a_complete_form": sum(1 for e in with_form if e["complete"]),
        "rows_without_a_form": len(without_form),
        "forms_with_no_catalog_row": orphans,
        "with_form": with_form,
        "without_form": without_form,
    }


def main() -> int:
    """CLI entry. Always returns 0 — this reports a gap, it does not fail on one."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=SEED)
    parser.add_argument("--forms", type=Path, default=FORMS_DIR)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = report(json.loads(args.seed.read_text()), load_forms(args.forms))

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(
        f"{result['rows_with_a_form']} of {result['catalog_rows']} catalog rows have a "
        f"real application form "
        f"({result['rows_with_a_complete_form']} of those transcribed in full).\n"
    )
    for entry in result["with_form"]:
        flag = "complete" if entry["complete"] else "PARTIAL"
        protected = ", ".join(entry["protected_fields"]) or "none"
        print(f"  {entry['id']}: {entry['fields']} fields [{flag}]")
        print(f"      source: {entry['form_source_url']} (read {entry['retrieved_at']})")
        print(f"      protected fields the agent may never fill: {protected}")
        if not entry["complete"]:
            print(f"      missing: {entry['completeness_note']}")
    if result["forms_with_no_catalog_row"]:
        print(f"\n  forms with no catalog row: {result['forms_with_no_catalog_row']}")
    print(
        f"\n  {result['rows_without_a_form']} row(s) can be discovered and judged but "
        f"not drafted, because no public form has been transcribed for them."
    )
    print(f"  {result['synthetic_forms']} synthetic [DEMO] form(s) present, excluded above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
