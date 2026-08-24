"""Reading structured facts off a page, with the sentence that said so.

Deterministic. No model call anywhere in this file, for the same reason
`agent/tools/eligibility.py` has none: a regex cannot be talked into seeing a
number that is not there, and every one of these decisions has to be
defensible to somebody who is about to spend eight hours on an application.

The contract each extractor honours:

    def find_x(blocks) -> tuple[value, Evidence] | None

`None` means the page did not say. It never means "probably". A caller that
receives `None` marks the field UNKNOWN through
`ScrapedOpportunity.set_field`, which is the only door into the record.

The evidence returned is the **block the value was read from**, verbatim.
That is what a reviewer reads instead of trusting the parse, and it is why
these heuristics are allowed to be imperfect: a wrong award figure with the
sentence attached is a two-second correction, while a wrong award figure on
its own is a lie with a citation-shaped hole where the source should be.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

from agent.scraping.models import Evidence

# ── Text into blocks ─────────────────────────────────────────────────────────

#: These pages lay prizes out as alternating short lines — "1st Place",
#: "$3000", "2nd Place", "$2000" — often with a blank line between each, so a
#: line is too small a unit of context and a paragraph is too large. A block
#: is a run of consecutive short lines glued together across blank lines,
#: with long lines left standing alone.
#:
#: Gluing across blanks is what makes an award figure legible: on its own,
#: "$3000" is a number with no meaning, and the only thing that makes it a
#: first prize is the two words above it.
_SHORT_LINE = 60
_MAX_BLOCK_CHARS = 600
#: Bounded so an unrelated list of short lines cannot glue into one wall of
#: text that then "supports" any figure inside it.
_MAX_GLUED_LINES = 12


def to_blocks(text: str) -> list[str]:
    """Split page text into context windows an evidence span can quote."""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    blocks: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            joined = " ".join(buffer).strip()
            if joined:
                blocks.append(joined[:_MAX_BLOCK_CHARS])
            buffer.clear()

    for line in lines:
        if len(line) <= _SHORT_LINE:
            buffer.append(line)
            if (
                len(buffer) >= _MAX_GLUED_LINES
                or sum(len(b) + 1 for b in buffer) > _MAX_BLOCK_CHARS
            ):
                flush()
        else:
            flush()
            blocks.append(line[:_MAX_BLOCK_CHARS])
    flush()
    return blocks


def _evidence(block: str, url: str, method: str) -> Evidence:
    return Evidence(text=block.strip(), source_url=url, method=method)


def _first(blocks: list[str], pattern: re.Pattern[str]) -> str | None:
    for block in blocks:
        if pattern.search(block):
            return block
    return None


# ── Money ────────────────────────────────────────────────────────────────────

_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?(\s?[KkMm]\b)?")

#: A dollar figure only counts as an award when its own block says so.
#: Without this, a parking fee and a first prize look identical.
_AWARD_CONTEXT = re.compile(
    r"\b(prize|prizes|award|awarded|awards|winner|winners|place|cash|"
    r"fellowship|grant|funding|receives?|purse|scholarship|stipend|"
    r"1st|2nd|3rd|first|second|third|runner)\b",
    re.I,
)

#: Distinguishes "a $50,000 pool split between six teams" from "you get
#: $50,000". Both are true sentences; only one is an award ceiling.
_POOL = re.compile(
    r"\b(total|in prizes|prize pool|combined|across|split|distributed|"
    r"awarded in total|pool of)\b",
    re.I,
)

#: Costs, fees and page furniture that sit next to a currency symbol.
_NOT_AN_AWARD = re.compile(
    r"\b(fee|fees|ticket|tickets|cost|costs|price|tuition|donat|per person|"
    r"admission|membership|revenue|valuation|raised|budget of)\b",
    re.I,
)


def _money_values(block: str) -> list[int]:
    values: list[int] = []
    for raw, suffix in _MONEY.findall(block):
        value = int(raw.replace(",", ""))
        if suffix and suffix.strip().lower() == "k":
            value *= 1_000
        elif suffix and suffix.strip().lower() == "m":
            value *= 1_000_000
        values.append(value)
    return values


def find_awards(blocks: list[str], url: str) -> dict:
    """Award floor and ceiling, each with the block it was read from.

    Returns a dict with any of `award_min`, `award_max`, `award_type` and
    `caveats`. A key that is absent means the page did not state it.
    """
    found: list[tuple[int, str]] = []
    pool: list[tuple[int, str]] = []

    for block in blocks:
        if _NOT_AN_AWARD.search(block) or not _AWARD_CONTEXT.search(block):
            continue
        values = _money_values(block)
        if not values:
            continue
        target = pool if _POOL.search(block) else found
        for value in values:
            target.append((value, block))

    result: dict = {"caveats": []}

    if found:
        low = min(found, key=lambda pair: pair[0])
        high = max(found, key=lambda pair: pair[0])
        result["award_min"] = (low[0], _evidence(low[1], url, "regex:award_block"))
        result["award_max"] = (high[0], _evidence(high[1], url, "regex:award_block"))
    elif pool:
        # Only a pool figure was stated. That is not an individual award, so
        # it is reported as a caveat and the award fields stay UNKNOWN.
        high = max(pool, key=lambda pair: pair[0])
        result["caveats"].append(
            f"Only a combined prize figure was found (${high[0]:,}); the page does "
            f"not state what one team receives. Award range left UNKNOWN. "
            f'Source text: "{high[1][:200]}"'
        )

    if found and pool:
        biggest_pool = max(pool, key=lambda pair: pair[0])
        biggest_award = max(found, key=lambda pair: pair[0])
        if biggest_pool[0] > biggest_award[0]:
            result["caveats"].append(
                f"The page also states ${biggest_pool[0]:,} as a combined or total "
                f"figure, which is larger than the largest individual award found "
                f"(${biggest_award[0]:,}). Confirm which number applies to one team."
            )

    award_type = _find_award_type(blocks, url)
    if award_type:
        result["award_type"] = award_type
    return result


_AWARD_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("non-cash prize", re.compile(r"\b(non-?cash|swag|in-?kind|credits?|no cash prize)\b", re.I)),
    ("fellowship", re.compile(r"\bfellowships?\b", re.I)),
    ("cash prize", re.compile(r"\b(cash prizes?|cash and prizes|prize money)\b", re.I)),
    ("grant", re.compile(r"\bgrants?\b", re.I)),
    ("scholarship", re.compile(r"\bscholarships?\b", re.I)),
    ("competition prize", re.compile(r"\b(prizes?|awards?)\b", re.I)),
)


def _find_award_type(blocks: list[str], url: str) -> tuple[str, Evidence] | None:
    for label, pattern in _AWARD_TYPE_PATTERNS:
        block = _first(blocks, pattern)
        if block:
            return label, _evidence(block, url, f"regex:award_type:{label}")
    return None


# ── Deadlines ────────────────────────────────────────────────────────────────

_MONTHS = "|".join(
    [m for m in calendar.month_name[1:]] + [m for m in calendar.month_abbr[1:]]
)
_DATE_FULL = re.compile(
    rf"\b({_MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I
)
_DATE_NO_YEAR = re.compile(rf"\b({_MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
_DATE_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

_DEADLINE_CONTEXT = re.compile(
    r"\b(deadline|due|closes?|closing|submit by|applications? (?:open|close|due)|"
    r"application opens?|final deadline|priority deadline|register by|"
    r"entries? due|last day)\b",
    re.I,
)

_MONTH_NUMBER = {
    name.lower(): index
    for index, name in enumerate(calendar.month_name)
    if name
} | {
    name.lower(): index
    for index, name in enumerate(calendar.month_abbr)
    if name
}


#: Words that mean "this date is when it closes", as opposed to when it
#: opens or when finalists are notified. A competition timeline lists all
#: three in a row and picking the first date is how a scraper reports an
#: opening date as a deadline.
_CLOSING_WORD = re.compile(
    r"\b(deadline|due|closes?|closing|submit by|last day|entries? due|"
    r"applications? (?:close|due))\b",
    re.I,
)
_OPENING_WORD = re.compile(
    r"\b(opens?|opening|application opens?|notification|announced|winners? "
    r"announced|finals?|pitch day|presentations? by)\b",
    re.I,
)

#: How far after a date to look for the word that classifies it. A timeline
#: renders as "Jan. 29th Final Deadline", so the label follows the date.
_LABEL_WINDOW = 45


def _classify(block: str, match: "re.Match[str]") -> str:
    """Is this date a closing date, an opening date, or unlabelled?"""
    after = block[match.end() : match.end() + _LABEL_WINDOW]
    before = block[max(0, match.start() - _LABEL_WINDOW) : match.start()]
    window = f"{before} {after}"
    if _CLOSING_WORD.search(window):
        return "closing"
    if _OPENING_WORD.search(window):
        return "opening"
    return "unlabelled"


def _parse(match: "re.Match[str]", numeric: bool) -> date | None:
    try:
        if numeric:
            return date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
        month = _MONTH_NUMBER.get(match.group(1).lower())
        return date(int(match.group(3)), month, int(match.group(2))) if month else None
    except (ValueError, TypeError):
        return None


def find_deadline(blocks: list[str], url: str) -> tuple[str, date | None, Evidence] | None:
    """The application deadline, verbatim, plus an ISO date when unambiguous.

    Two rules keep this from confidently reporting the wrong day.

    *   **A date labelled "opens" is not a deadline.** Competition pages lay
        out a timeline — opens, priority deadline, final deadline, finals —
        and the first date on the page is usually the one you least want.
        Dates are classified by the words around them and a closing date
        always beats an unlabelled one.
    *   **A month and day with no year does not become a date.** "Nov. 1st"
        could be this year or next. The verbatim string is kept and
        `deadline_iso` stays None rather than a coin flip landing in a field
        that looks calculated.
    """
    candidates = [b for b in blocks if _DEADLINE_CONTEXT.search(b)]
    if not candidates:
        return None

    #: (rank, verbatim, parsed, block, method). Lower rank wins.
    found: list[tuple[int, str, date | None, str, str]] = []

    for block in candidates:
        for pattern, numeric, method in (
            (_DATE_FULL, False, "deadline_with_year"),
            (_DATE_NUMERIC, True, "deadline_numeric"),
        ):
            for match in pattern.finditer(block):
                kind = _classify(block, match)
                rank = {"closing": 0, "unlabelled": 1, "opening": 3}[kind]
                found.append(
                    (rank, match.group(0), _parse(match, numeric), block, f"regex:{method}:{kind}")
                )

    if not found:
        # Same ordering, but nothing carried a year, so nothing resolves.
        for block in candidates:
            for match in _DATE_NO_YEAR.finditer(block):
                kind = _classify(block, match)
                rank = {"closing": 0, "unlabelled": 1, "opening": 3}[kind]
                found.append(
                    (rank, match.group(0), None, block, f"regex:deadline_no_year:{kind}(unresolved)")
                )

    if found:
        rank, verbatim, parsed, block, method = min(found, key=lambda row: row[0])
        return verbatim, parsed, _evidence(block, url, method)

    # A deadline is discussed but no date is stated. Say that, rather than
    # letting the field look simply absent.
    return (
        "stated on the page without a specific date",
        None,
        _evidence(candidates[0], url, "regex:deadline_context_only"),
    )


def deadline_is_ambiguous(blocks: list[str]) -> str | None:
    """A caveat when the page lists several dates and we picked one.

    Returned so the reviewer sees the whole timeline rather than trusting
    that a single extracted date was the only one on offer.
    """
    dates: list[str] = []
    for block in blocks:
        if not _DEADLINE_CONTEXT.search(block):
            continue
        for pattern in (_DATE_FULL, _DATE_NUMERIC, _DATE_NO_YEAR):
            dates.extend(m.group(0) for m in pattern.finditer(block))
    unique = list(dict.fromkeys(dates))
    if len(unique) > 1:
        return (
            "The page lists more than one date in a deadline context "
            f"({', '.join(unique[:8])}). The one recorded above is the earliest "
            "date the page labels as a closing date; confirm which applies to you."
        )
    return None


# ── Who may apply ────────────────────────────────────────────────────────────

_ELIGIBILITY_CONTEXT = re.compile(
    r"\b(eligib\w*|who can (?:participate|apply|enter)|open to|must be|"
    r"restricted to|enrolled|participants?|applicants?|requirements?|"
    r"who is eligible)\b",
    re.I,
)

_DEGREE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("undergraduate", re.compile(r"\bundergraduate?s?\b|\bundergrads?\b", re.I)),
    ("graduate", re.compile(r"\bgraduate students?\b|\bgrad students?\b", re.I)),
    ("masters", re.compile(r"\bmaster'?s\b|\bmasters\b", re.I)),
    ("mba", re.compile(r"\bMBA\b")),
    ("phd", re.compile(r"\bPh\.?D\.?\b|\bdoctoral\b", re.I)),
    ("postdoc", re.compile(r"\bpost-?docs?\b|\bpostdoctoral\b", re.I)),
    ("alumni", re.compile(r"\balumni\b|\balumnus\b|\brecent graduates?\b", re.I)),
)


#: Phrases that mark a block as an actual eligibility statement rather than a
#: heading or a sponsor list that happens to sit next to one. Used to choose
#: *which* matching block gets quoted as evidence, never to change the value.
_STRONG_ELIGIBILITY = re.compile(
    r"\b(open to|eligib\w*|must be|enrolled|restricted to|who can (?:participate|apply)|"
    r"available to|limited to|required to be)\b",
    re.I,
)


def _scan_eligibility(
    blocks: list[str],
    patterns: tuple[tuple[str, "re.Pattern[str]"], ...],
    url: str,
    method: str,
) -> tuple[list[str], Evidence] | None:
    """Union the labels matched across every eligibility block on the page.

    Taking only the first matching block loses real rules: a page can state
    "open to all RBS students" in one place and name the leadership
    categories in another, and a founder who reads only the first one applies
    for something they cannot lead.

    The value is the union. The **evidence** is the single most explicit
    block that contributed — the one carrying an "open to" or "eligible"
    phrase, with the most distinct labels in it — because a reviewer needs
    one quotable sentence, not a pile of them. Every other contributing
    block is still on the page the `source_url` points at.
    """
    labels: list[str] = []
    best: tuple[int, str] | None = None

    for block in blocks:
        if not _ELIGIBILITY_CONTEXT.search(block):
            continue
        matched = [label for label, pattern in patterns if pattern.search(block)]
        if not matched:
            continue
        for label in matched:
            if label not in labels:
                labels.append(label)
        score = len(matched) + (3 if _STRONG_ELIGIBILITY.search(block) else 0)
        if best is None or score > best[0]:
            best = (score, block)

    if not labels or best is None:
        return None
    return labels, _evidence(best[1], url, method)


def find_degree_levels(blocks: list[str], url: str) -> tuple[list[str], Evidence] | None:
    """Degree levels named anywhere the page discusses who may apply.

    Restricted to eligibility blocks on purpose. "Our alumni network" in a
    marketing paragraph is not an eligibility rule, and a scan of the whole
    page would read it as one.
    """
    return _scan_eligibility(blocks, _DEGREE_PATTERNS, url, "regex:eligibility_block")


_INSTITUTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Rutgers University", re.compile(r"\bRutgers\b", re.I)),
    ("New Jersey Institute of Technology", re.compile(r"\bNJIT\b|New Jersey Institute of Technology", re.I)),
    ("Stevens Institute of Technology", re.compile(r"\bStevens\b", re.I)),
    ("Princeton University", re.compile(r"\bPrinceton\b", re.I)),
)

_OPEN_TO_MANY = re.compile(
    r"\b(any (?:accredited )?(?:college|university)|all New Jersey|statewide|"
    r"northern new jersey (?:colleges|universities)|colleges and universities|"
    r"students from (?:any|all))\b",
    re.I,
)


def find_institutions(blocks: list[str], url: str) -> tuple[list[str], Evidence] | None:
    """Institutions named as eligible.

    A page that says "open to students at Northern NJ colleges" is recorded
    with that phrase as evidence rather than expanded into a list of schools.
    Expanding it would be inference, and the phrase is what the reviewer
    needs to see anyway.
    """
    for block in blocks:
        if _ELIGIBILITY_CONTEXT.search(block) and _OPEN_TO_MANY.search(block):
            names = [n for n, p in _INSTITUTION_PATTERNS if p.search(block)]
            return (
                names or ["stated as open beyond one institution — see evidence"],
                _evidence(block, url, "regex:institution_open_scope"),
            )
    return _scan_eligibility(blocks, _INSTITUTION_PATTERNS, url, "regex:eligibility_block")


_APPLICANT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("student founder", re.compile(r"\b(student (?:founder|entrepreneur)s?|student-?led|student venture)\b", re.I)),
    ("student team", re.compile(r"\bteams?\b", re.I)),
    ("student", re.compile(r"\bindividuals?\b|\bstudents?\b", re.I)),
    ("alumni", re.compile(r"\balumni\b|\brecent graduates?\b", re.I)),
    ("faculty", re.compile(r"\bfaculty\b", re.I)),
)


def find_applicant_types(blocks: list[str], url: str) -> tuple[list[str], Evidence] | None:
    return _scan_eligibility(blocks, _APPLICANT_PATTERNS, url, "regex:eligibility_block")


_TEAM_RANGE = re.compile(
    r"\bteams?\s+(?:can\s+)?(?:consist(?:ing)?\s+of|of|may\s+have|must\s+have|"
    r"comprised?\s+of|be)?\s*(?:up\s+to\s+)?(\d+)\s*(?:to|-|–|through)\s*(\d+)\s*"
    r"(?:members|people|students|participants)?",
    re.I,
)
_TEAM_MAX = re.compile(
    r"\b(?:teams?|groups?|individuals? or teams?)\s+of\s+up\s+to\s+"
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.I,
)
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def find_team_size(blocks: list[str], url: str) -> dict:
    """Team minimum and maximum, when the page states them."""
    for block in blocks:
        match = _TEAM_RANGE.search(block)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            evidence = _evidence(block, url, "regex:team_range")
            return {"team_size_min": (low, evidence), "team_size_max": (high, evidence)}
    for block in blocks:
        match = _TEAM_MAX.search(block)
        if match:
            raw = match.group(1).lower()
            value = _WORD_NUMBERS.get(raw, None) or (int(raw) if raw.isdigit() else None)
            if value:
                return {"team_size_max": (value, _evidence(block, url, "regex:team_max"))}
    return {}


# ── Equity ───────────────────────────────────────────────────────────────────

_EQUITY_TAKEN = re.compile(
    r"\b(in exchange for equity|equity stake|takes? \d+%|for \d+% of|"
    r"convertible note|SAFE (?:note|agreement)|investment in exchange)\b",
    re.I,
)
_EQUITY_FREE = re.compile(
    r"\b(no equity|equity-?free|non-?dilutive|without taking equity|"
    r"we take no (?:equity|ownership)|no strings attached)\b",
    re.I,
)


def find_equity(blocks: list[str], url: str) -> tuple[bool, Evidence] | None:
    """Whether the funder takes equity.

    Only when the page says so. A pitch competition almost certainly hands
    over a cheque with no equity attached, and "almost certainly" is exactly
    the reasoning this pipeline refuses to do. Silence stays UNKNOWN.
    """
    for block in blocks:
        if _EQUITY_FREE.search(block):
            return False, _evidence(block, url, "regex:equity_free")
    for block in blocks:
        if _EQUITY_TAKEN.search(block):
            return True, _evidence(block, url, "regex:equity_taken")
    return None


# ── Caveats a reviewer must not miss ─────────────────────────────────────────

_CAVEAT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "conditional eligibility",
        re.compile(
            r"\b(some restrictions apply|restrictions apply|leadership roles?|"
            r"must be filled by|only .{0,40} are eligible to lead|"
            r"eligible categories)\b",
            re.I,
        ),
    ),
    (
        "indirect access route",
        re.compile(
            r"\b(will be (?:chosen|selected) at|selected to represent|"
            r"advance to|feeds? into|qualif\w+ (?:through|via)|"
            r"required for selection|represent \w+ at)\b",
            re.I,
        ),
    ),
    (
        "non-cash award",
        re.compile(r"\b(non-?cash|no cash prize|in-?kind|swag|credits?)\b", re.I),
    ),
    (
        "host-institution scope",
        re.compile(
            r"\b(senior design|capstone teams?|open (?:only )?to \w+ students only|"
            r"must be enrolled at)\b",
            re.I,
        ),
    ),
)


def find_caveats(blocks: list[str], url: str) -> list[str]:
    """Sentences that change who can realistically apply.

    These do not fit a structured field but they decide whether an
    opportunity is real for a given founder — "Rutgers students may compete,
    but the leadership roles must be RBS seniors" is the difference between
    an application and a waste of an afternoon.
    """
    out: list[str] = []
    seen: set[str] = set()
    for label, pattern in _CAVEAT_PATTERNS:
        for block in blocks:
            if pattern.search(block) and block not in seen:
                seen.add(block)
                out.append(f'[{label}] "{block[:300]}"')
                break
    return out


# ── Organisation ─────────────────────────────────────────────────────────────

#: "Sponsored by" is deliberately absent. The Sales Executives Club funds the
#: RBS competition; Rutgers Business School runs it, and a founder who writes
#: to the sponsor has written to the wrong people.
_HOSTED_BY = re.compile(
    r"\b(?:hosted|presented|organized|organised|run) by\s+"
    # A program name routinely contains commas ("Innovation, Design, and
    # Entrepreneurship Academy"), so only a sentence end or a parenthetical
    # terminates the capture.
    r"(?:the\s+)?([A-Z][^.;()]{3,80})",
    re.I,
)

#: Page furniture that gets swept into a capture when a heading follows the
#: organiser's name. A capture containing any of these is not a name.
_NOT_A_NAME = re.compile(
    r"\b(contact|location|participation|eligibility|apply|register|"
    r"deadline|email|phone|click|learn more)\b",
    re.I,
)


def _clean_org(name: str) -> str | None:
    cleaned = name.strip().strip(",;:-–— ").strip()
    if _NOT_A_NAME.search(cleaned) or len(cleaned) < 4:
        return None
    return cleaned


def find_organization(blocks: list[str], url: str, fallback: str) -> tuple[str, Evidence]:
    """Who runs the program, from the page's own words when it says.

    Falls back to the registry's value rather than to a guess — the fallback
    was written by a human reading the site, which is a different thing from
    a parser inventing one.
    """
    for block in blocks:
        match = _HOSTED_BY.search(block)
        if match:
            cleaned = _clean_org(match.group(1))
            if cleaned:
                return cleaned, _evidence(block, url, "regex:hosted_by")
    return fallback, Evidence(
        text=f"Not stated in a parseable form on the page; taken from the target registry: {fallback}",
        source_url=url,
        method="registry_fallback",
    )
