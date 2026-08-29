"""The review document.

The candidate JSON is what the pipeline talks to. This is what the founder
reads before deciding where eight hours go, so it is organised around that
decision rather than around the schema:

    what kind of application · what it funds · what the money is
    · what it requires · what past applicants said · what we do not know

The last two matter most and are the easiest to fake, so neither is ever
generated. **Founder reviews are always empty** — no target page publishes
them — and every field the page did not state is printed as `UNKNOWN`
in place, not quietly omitted. A reader has to be able to tell the difference
between "this program takes no equity" and "nobody wrote down whether this
program takes equity", because only one of those is a fact.
"""

from __future__ import annotations

from pathlib import Path

from agent.scraping.models import ScrapedOpportunity, ScrapeRun

UNKNOWN = "**UNKNOWN** — the page does not state this. Not inferred."


def _fmt_award(record: ScrapedOpportunity) -> str:
    """Award range for the review document, or the UNKNOWN marker.

    A half-known range renders the missing end as UNKNOWN rather than as an
    open interval, so a reviewer sees which end the page actually stated.
    """
    if record.award_min is None and record.award_max is None:
        return UNKNOWN
    if record.award_min == record.award_max:
        return f"${record.award_max:,}"
    low = f"${record.award_min:,}" if record.award_min is not None else "UNKNOWN"
    high = f"${record.award_max:,}" if record.award_max is not None else "UNKNOWN"
    return f"{low} – {high}"


def _fmt_list(values: list[str] | None) -> str:
    """Comma-joined values, or UNKNOWN. An empty list and None render the same — both mean the page gave nothing."""
    return ", ".join(values) if values else UNKNOWN


def _fmt_bool(value: bool | None, yes: str, no: str) -> str:
    """Three-valued rendering: `yes`, `no`, or UNKNOWN for None.

    The None branch is the point. A bool that renders False for "unstated"
    is how a review document tells a reviewer the page said something it
    never said.
    """
    if value is None:
        return UNKNOWN
    return yes if value else no


def _fmt_team(record: ScrapedOpportunity) -> str:
    """Team size range, with each missing end shown as UNKNOWN rather than filled in."""
    if record.team_size_min is None and record.team_size_max is None:
        return UNKNOWN
    low = record.team_size_min if record.team_size_min is not None else "UNKNOWN"
    high = record.team_size_max if record.team_size_max is not None else "UNKNOWN"
    return f"{low} – {high} members"


def _fmt_deadline_iso(record: ScrapedOpportunity) -> str:
    """Three outcomes, and they mean different things.

    No deadline found at all is not the same as a deadline written without a
    year. Collapsing them would tell a reader the page was vague when it was
    actually silent.
    """
    if record.deadline_iso:
        return record.deadline_iso.isoformat()
    if record.deadline is None:
        return UNKNOWN
    return (
        "**UNRESOLVED** — the page gives this date without a year, so no calendar "
        "date was derived. Guessing the year is exactly the inference this pipeline "
        "does not make."
    )


def _evidence_rows(record: ScrapedOpportunity) -> str:
    """The evidence table: every extracted field beside the text it was read from.

    This is the part of the review document a person actually checks — the
    claim is only as good as the quote under it. Pipes are escaped and
    newlines flattened so a quote cannot break the Markdown table, and quotes
    over 400 characters are truncated with an ellipsis, so a very long block
    is abbreviated here while the full span stays in the record.
    """
    if not record.evidence:
        return "_No evidence spans were captured for this row._\n"
    lines = ["| Field | Quoted from the page | Found by |", "|---|---|---|"]
    for field, evidence in record.evidence.items():
        quote = evidence.text.replace("|", "\\|").replace("\n", " ")
        if len(quote) > 400:
            quote = quote[:400].rstrip() + "…"
        lines.append(f"| `{field}` | {quote} | `{evidence.method}` |")
    return "\n".join(lines) + "\n"


def render_opportunity(record: ScrapedOpportunity, index: int) -> str:
    """Render one candidate row as a Markdown review section.

    Written for a person deciding ACCEPT or REJECT, so it leads with what is
    *not* known: unknown fields and caveats are rendered explicitly rather
    than omitted, because a field silently missing from a document reads as a
    field that did not matter.
    """
    source = record.source_url or "_no source URL — see caveats_"
    unknown_list = (
        ", ".join(f"`{f}`" for f in record.unknown_fields)
        if record.unknown_fields
        else "none — every field on this row was read off the page"
    )

    caveats = (
        "\n".join(f"- {c}" for c in record.caveats)
        if record.caveats
        else "- None recorded."
    )

    reviews = (
        "\n".join(
            f"- \"{r.text}\" — {r.attribution} (entered by {r.added_by})"
            for r in record.founder_reviews
        )
        if record.founder_reviews
        else (
            "- **None available.** No page in this target set publishes reviews from "
            "past student applicants, and the scraper does not write this field. "
            "Anything that appears here was typed in by a person. Treat the absence "
            "as missing information, not as a bad sign about the program."
        )
    )

    return f"""
## {index}. {record.title}

**Run by:** {record.organization}
**Source:** <{source}>
**Scraped:** {record.scraped_at.isoformat()}
**Review status:** `{record.review_status}`

### What kind of application is it

{record.award_type or UNKNOWN}

### What the money looks like

| | |
|---|---|
| Award range | {_fmt_award(record)} |
| Deadline as written | {record.deadline or UNKNOWN} |
| Deadline as a date | {_fmt_deadline_iso(record)} |
| Equity taken | {_fmt_bool(record.equity_required, "Yes — the page states equity is involved", "No — the page states this is equity-free")} |

### What it requires

| | |
|---|---|
| Institutions | {_fmt_list(record.institution)} |
| Degree levels | {_fmt_list(record.degree_levels)} |
| Applicant type | {_fmt_list(record.applicant_type)} |
| Team size | {_fmt_team(record)} |

### What past student founders said

{reviews}

### Read this before applying

{caveats}

### What we do not know

{unknown_list}

### Evidence — every field above, traced to the page

{_evidence_rows(record)}
---
"""


def render(records: list[ScrapedOpportunity], run: ScrapeRun) -> str:
    """The whole document."""
    failures = (
        "\n".join(
            f"- `{f.url or '(no url)'}` — {f.failure}" for f in run.failures
        )
        or "- None. Every target answered."
    )
    notes = "\n".join(f"- {n}" for n in run.notes) or "- None."

    body = "".join(
        render_opportunity(record, index)
        for index, record in enumerate(sorted(records, key=lambda r: r.title), start=1)
    )

    return f"""# Rutgers student funding — candidate opportunities for review

**Nothing in this document is production data.** Every row below was read off
a public web page by a deterministic scraper, and every row is waiting on a
human. None of it has been written to `data/opportunities.seed.json`, and
nothing will be until someone reads the evidence and says so.

{run.headline()}

Run `{run.run_id}`, finished {run.finished_at.isoformat() if run.finished_at else "—"}.

---

## How to read this

Three things are worth knowing before you trust a single number here.

1. **`UNKNOWN` means the page did not say it.** It does not mean "no
   restriction", it does not mean "probably fine", and it was not filled in
   from anywhere else. If a program's page never mentions equity, the equity
   row says UNKNOWN — not "no equity" — even where that would almost
   certainly be right. Almost certainly right is how a wrong fact ends up on
   a real application.
2. **Every value carries the sentence it came from.** The evidence table at
   the end of each entry is the actual page text. Where a number looks wrong,
   read the quote: the parse is a convenience and the quote is the source.
3. **Founder reviews are empty everywhere, and that is not a finding.** No
   page in this target set publishes them. The scraper has no code path that
   writes that field. If you want reviews in here, they have to come from
   people you talk to.

## What was not collected, and why

- **Nothing behind a login.** No target was authenticated against, no form
  was submitted, and no CAPTCHA was touched. There is no code path for any of
  it.
- **Nothing robots.txt disallowed.** Each host's robots.txt was fetched
  before its pages were, archived under `data/raw/robots/`, and an
  unreachable robots.txt was treated as a refusal rather than as permission.
- **No speculative browser rendering.** Pages were fetched as static HTML.
  A headless browser is used only where a static fetch already proved the
  page returns a JavaScript shell, and only when explicitly asked for.

### Targets that produced nothing this run

{failures}

### Run notes

{notes}

---
{body}
## What a human still has to do

1. Open each `source_url` and check the evidence quotes still match the page.
2. Resolve the `UNKNOWN` fields that matter for your situation — usually
   degree level, deadline year and whether the award is per team.
3. Fill in `founder_reviews` from people who actually competed.
4. Set `review_status` to `ACCEPTED` or `REJECTED` in
   `data/opportunities.rutgers.candidates.json`.
5. Only then move accepted rows into `data/opportunities.candidates.json` and
   run `uv run python scripts/verify_seed.py`.
"""


def write_review_doc(
    records: list[ScrapedOpportunity], run: ScrapeRun, path: Path
) -> Path:
    """Render every record to Markdown and write it to `path`, creating parent directories.

    Overwrites unconditionally — the review document is a regenerated view of
    the candidate file, not a place to keep notes. A reviewer's decisions
    belong in `review_status` on the row.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(records, run))
    return path
