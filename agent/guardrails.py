"""Hard restrictions and the single ship gate.

Everything in this file is enforced in **Python, outside the model**, so no
prompt injection and no clever phrasing can unlock it. None of it is tunable
by a prompt and none of it is overridable by a founder asking nicely.

Two halves:

*   **Thresholds** (Section 10.7) — the constants that decide what is worth a
    founder's attention. They live here, together, with their reasoning
    written next to them, because "how does it decide what's worth my time?"
    should be answered by opening one file.
*   **`ship_gate`** (Section 11.9) — eight ordered checks that a draft must
    pass before its status can become `READY`. Fail closed: the first failure
    stops the chain, and an exception inside the gate is never read as a pass.

On the heuristics below (numeric whitelist, entity check, closed-world check):
they are regex-and-set-membership, not semantics. They will produce some
false positives. Every false positive pushes a field to `NEEDS_FOUNDER`,
which is the safe direction — the founder answers one extra question instead
of submitting one invented fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from agent.models import (
    Assessment,
    AuditReport,
    Draft,
    DraftField,
    GateResult,
    GateViolation,
    KnowledgeBase,
    Opportunity,
)

# ═════════════════════════════════════════════════════════════════════════════
# Section 10.7 — what counts as "a real decision"
# ═════════════════════════════════════════════════════════════════════════════

#: A digest is a briefing, not a feed. Three is what a founder will actually
#: read between classes. Everything past this goes to a passive "also found"
#: list with no notification, ranked by value-per-hour.
MAX_SURFACED_PER_RUN = 3

#: A surfaced opportunity whose deadline lands inside this window with no
#: action taken earns one more nudge. This is the single exception to
#: never-notify-twice, because a missed deadline is the failure the whole
#: product exists to prevent.
URGENT_DAYS = 7

#: When eligibility could not be determined and the award is at least this
#: large, it is worth the founder spending two minutes emailing the program
#: to find out. Below it, the expected value does not justify the interrupt.
HIGH_VALUE_THRESHOLD_USD = 10_000

#: Hours per day a student founder realistically has for an application,
#: between coursework and building. Used to decide whether a deadline is
#: physically reachable. Deliberately pessimistic: telling someone about
#: something they cannot finish is noise, not help.
REALISTIC_HOURS_PER_DAY = 1.5

#: Below this many knowledge chunks the Drafter is disabled entirely
#: (Section 11.10). The agent still discovers, filters and assesses — it just
#: does not write prose it cannot ground.
MIN_KB_CHUNKS = 5

#: Hard cap on opportunities the Assessor may judge in one run. A cost
#: control, and a latency control. Overflow is reported in the RunReport,
#: never silently dropped.
MAX_ASSESSMENTS_PER_RUN = 25


@dataclass(frozen=True)
class SurfaceDecision:
    """Should this reach the human, and why or why not."""

    surface: bool
    kind: str
    reason: str


def days_until(deadline: date | None, today: date) -> int | None:
    """Date arithmetic in Python. A model never computes a countdown.

    Models are bad at date math and confidently wrong about it
    (Section 9, rule 8).
    """
    return None if deadline is None else (deadline - today).days


def is_reachable(
    effort_hours: float, deadline: date | None, today: date
) -> bool:
    """Can the founder physically finish this before it closes?"""
    remaining = days_until(deadline, today)
    if remaining is None:  # rolling deadline
        return True
    if remaining < 0:
        return False
    return remaining * REALISTIC_HOURS_PER_DAY >= effort_hours


def escalation_decision(
    *,
    assessment: Assessment,
    opportunity: Opportunity,
    eligibility: str,
    max_application_hours: int,
    min_award: int,
    today: date,
    already_surfaced: bool,
) -> SurfaceDecision:
    """The whole of Section 10.7 as one function.

    Silence is a valid output. The counters still record the work.
    """
    if already_surfaced:
        return SurfaceDecision(False, "", "already surfaced — never notify twice")

    remaining = days_until(opportunity.deadline, today)
    if remaining is not None and remaining < 0:
        return SurfaceDecision(False, "", "deadline has passed")

    if assessment.effort_hours > max_application_hours:
        return SurfaceDecision(
            False,
            "",
            f"needs ~{assessment.effort_hours:.0f}h, founder's ceiling is "
            f"{max_application_hours}h",
        )

    if not is_reachable(assessment.effort_hours, opportunity.deadline, today):
        return SurfaceDecision(
            False,
            "",
            f"{remaining}d left at {REALISTIC_HOURS_PER_DAY}h/day cannot cover "
            f"~{assessment.effort_hours:.0f}h of work",
        )

    award = opportunity.best_award
    if award is not None and award < min_award:
        return SurfaceDecision(
            False, "", f"award ${award:,} is below the founder's floor ${min_award:,}"
        )

    if assessment.verdict == "APPLY":
        return SurfaceDecision(True, "APPLY", assessment.reason)

    if assessment.verdict == "MAYBE":
        # A MAYBE only earns an interrupt when the founder can act on the
        # blocker. "Requires a faculty PI" is actionable. "Restricted to
        # PhD students" is not.
        if assessment.blocker_founder_resolvable:
            return SurfaceDecision(True, "MAYBE", assessment.reason)
        return SurfaceDecision(
            False, "", f"MAYBE with a blocker the founder cannot resolve: {assessment.blocker}"
        )

    if assessment.verdict == "INSUFFICIENT_INFO":
        # Abstention is not a filter-out. It surfaces as "needs a human look"
        # when the money justifies it (Section 11.6).
        if award is not None and award >= HIGH_VALUE_THRESHOLD_USD:
            return SurfaceDecision(
                True,
                "UNKNOWN_HIGH_VALUE",
                "Could not confirm eligibility from the program's own page. "
                f"At ${award:,} it is worth a two-minute email before you spend hours.",
            )
        return SurfaceDecision(
            False, "", "insufficient info, and award is below the high-value threshold"
        )

    if eligibility == "UNKNOWN" and award is not None and award >= HIGH_VALUE_THRESHOLD_USD:
        return SurfaceDecision(
            True,
            "UNKNOWN_HIGH_VALUE",
            "Eligibility could not be confirmed from the source text.",
        )

    return SurfaceDecision(False, "", assessment.reason or "SKIP")


def rank_key(
    assessment: Assessment, opportunity: Opportunity
) -> float:
    """Value per hour: `(award x fit) / effort`. Computed in Python.

    Used to decide which three of the surfaceable items actually notify and
    which fall into the passive list.
    """
    award = float(opportunity.best_award or 0)
    fit = {"APPLY": 1.0, "MAYBE": 0.5, "INSUFFICIENT_INFO": 0.25, "SKIP": 0.0}[
        assessment.verdict
    ]
    effort = max(assessment.effort_hours, 0.5)
    return (award * fit) / effort


# ═════════════════════════════════════════════════════════════════════════════
# Section 10.1 — fields the agent must never fill
# ═════════════════════════════════════════════════════════════════════════════

#: If a field label matches, its status is forced to NEEDS_FOUNDER **even
#: when the answer is known**. False statements on a federal funding
#: application are a legal exposure for the founder, not a UX inconvenience.
#: An agent that auto-checks a certification box is a liability.
FIELD_BLOCKLIST: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("attestation", re.compile(r"\b(certif\w*|attest\w*|assur\w*|affirm\w*)\b", re.I)),
    ("attestation", re.compile(r"under penalty|legal representation|represent(?:s|ation)? and warrant", re.I)),
    ("signature", re.compile(r"\b(e-?signature|signature|signed by|sign here|initials?)\b", re.I)),
    ("authorization", re.compile(r"authorized (?:organization )?representative|\bAOR\b", re.I)),
    ("disclosure", re.compile(r"\b(debarment|debarred|conflict of interest|lobbying)\b", re.I)),
    # A form that says "Disclosure Form" and nothing else is still a
    # disclosure. Found by transcribing a real one: MIT CEP's "IP, Capital,
    # and Revenue Disclosure Forms" tripped none of the patterns above.
    ("disclosure", re.compile(r"\bdisclosure(?:s)?\b", re.I)),
    ("tax_id", re.compile(r"\b(ssn|social security|itin|ein|employer identification|tax\s*id|uei|sam\.gov|duns|cage code)\b", re.I)),
    ("payment", re.compile(r"\b(bank account|routing number|account number|wire|ach|payment details)\b", re.I)),
)


def blocklisted(label: str) -> str | None:
    """Return the blocklist category a field label trips, or None."""
    for category, pattern in FIELD_BLOCKLIST:
        if pattern.search(label):
            return category
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Section 10.2 — facts the agent must never invent
# ═════════════════════════════════════════════════════════════════════════════

#: Category -> (trigger in generated text, evidence pattern required in the KB).
#: If the draft asserts one of these and nothing in the knowledge base
#: supports the same category *with the same polarity*, the draft hard-fails.
#: Not a warning — a fail. Polarity matters: "there is no faculty advisor"
#: contains the words "faculty advisor" and supports nothing except the
#: absence of one. See `evidence_supports_claim`.
FORBIDDEN_CLAIMS: tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...] = (
    (
        "faculty_sponsor",
        re.compile(r"\b(faculty advisor|faculty sponsor|principal investigator|\bPI\b|letter of support|institutional sponsor|advised by|mentored by)\b", re.I),
        re.compile(r"\b(faculty advisor|faculty sponsor|principal investigator|letter of support|advis\w+|mentor\w+)\b", re.I),
    ),
    (
        "incorporation",
        re.compile(r"\b(incorporated|LLC|C-?Corp|S-?Corp|501\(c\)\(3\)|nonprofit|Delaware|entity was formed|registered (?:business|company))\b", re.I),
        re.compile(r"\b(incorporat\w+|LLC|C-?Corp|S-?Corp|501\(c\)\(3\)|nonprofit|formed|registered)\b", re.I),
    ),
    (
        "traction",
        re.compile(r"\b(users?|customers?|pilots?|interviews?|retention|revenue|MRR|ARR|waitlist|signups?|downloads?)\b", re.I),
        re.compile(r"\b(users?|customers?|pilots?|interviews?|retention|revenue|MRR|ARR|waitlist|signups?|downloads?)\b", re.I),
    ),
    (
        "prior_funding",
        re.compile(r"\b(awarded|grant(?:ed)?|prize|fellowship|raised|pre-?seed|angel|accelerator|featured in|press coverage|partnership with|partnered with)\b", re.I),
        re.compile(r"\b(award\w*|grant\w*|prize|fellowship|rais\w+|pre-?seed|angel|accelerator|press|partner\w*)\b", re.I),
    ),
    (
        "credentials",
        re.compile(r"\b(PhD|Ph\.D\.|M\.?D\.?|MBA|Professor|Dr\.|CTO|CEO|co-?founder|postdoc)\b", re.I),
        re.compile(r"\b(PhD|Ph\.D\.|M\.?D\.?|MBA|Professor|Dr\.|CTO|CEO|co-?founder|postdoc)\b", re.I),
    ),
    (
        "ip_status",
        re.compile(r"\b(patent(?:ed|s)?|provisional|trademark|licensed|license agreement|IP portfolio)\b", re.I),
        re.compile(r"\b(patent\w*|provisional|trademark|licens\w+|IP)\b", re.I),
    ),
)


# ── Polarity-aware evidence support ──────────────────────────────────────────

#: Clause boundaries: sentence punctuation, commas, and contrast conjunctions.
#: Commas matter — "no revenue yet, but 40 users" is one sentence whose two
#: clauses carry opposite polarities, and a negation window that ignores the
#: comma blocks the supported half.
_CLAUSE_BOUNDARY = re.compile(
    r"[.;!?\n,]+|\b(?:but|however|although|though|whereas|except)\b", re.I
)

#: Deterministic negation markers. Words, contractions (both apostrophes),
#: and the "has yet to" family. Deliberately not a dependency parse — a
#: marker anywhere in the clause negates the whole clause, which errs toward
#: withholding, the direction the design accepts.
_NEGATION_MARKER = re.compile(
    r"\b(?:no|not|never|neither|nor|none|cannot|without|lacks?|lacked|lacking)\b"
    r"|\b\w+n['’]t\b"
    r"|\byet\s+to\b",
    re.I,
)


def _clauses(text: str) -> list[str]:
    """Split text into clauses for per-clause negation scoring.

    Splitting matters: negation is scoped to the clause it appears in, so
    "no revenue, but 40 users" reads as two claims with opposite polarity
    rather than one negated sentence. Empty and whitespace-only fragments are
    dropped so a trailing separator does not produce a phantom clause.
    """
    return [c for c in _CLAUSE_BOUNDARY.split(text or "") if c and c.strip()]


def _polarities(text: str, pattern: re.Pattern[str]) -> set[bool]:
    """Per clause the pattern matches in: is that clause negated?

    Returns a set because a text can assert both polarities of one category
    ("no revenue, but 40 users").
    """
    return {
        bool(_NEGATION_MARKER.search(clause))
        for clause in _clauses(text)
        if pattern.search(clause)
    }


def evidence_supports_claim(
    answer: str, trigger: re.Pattern[str], evidence: re.Pattern[str], kb_text: str
) -> bool:
    """Does the knowledge base support this claim *at the claim's polarity*?

    Every polarity the answer asserts for the category must appear at the
    same polarity somewhere in the evidence. A positive claim needs a
    non-negated evidence clause; a claim of absence needs a negated one.
    Keyword overlap inside a clause of the opposite polarity is not support —
    it is usually the refutation.

    Deterministic clause-and-marker analysis, not semantics. When it cannot
    establish support it fails closed: the field blocks and goes back to the
    founder.
    """
    claimed = _polarities(answer, trigger)
    if not claimed:
        # The trigger fired on the whole answer but on no single clause
        # (a phrase broken by a clause boundary). Treat it as a positive
        # claim so evidence is still required.
        claimed = {False}
    return claimed <= _polarities(kb_text, evidence)


# ═════════════════════════════════════════════════════════════════════════════
# Section 11.4 — numeric whitelist
# ═════════════════════════════════════════════════════════════════════════════

#: `$5,000` `5K` `1.2M` `40%` `40`, plus spelled-out forms: `forty users`,
#: `twenty-five`, `one hundred applicants`, `two thousand dollars`,
#: `forty-five percent`, and mixed `1.5 million`. Everything normalises to a
#: comparable float, so the whitelist is symmetric between digit and word
#: forms in both the draft and the knowledge base.
_NUMBER = re.compile(r"\$?\s?(\d[\d,]*(?:\.\d+)?)\s?([KkMm])?%?")

_WORD_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_WORD_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_WORD_SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}

_NUMBER_WORDS = sorted(
    {*_WORD_UNITS, *_WORD_TENS, "hundred", *_WORD_SCALES}, key=len, reverse=True
)
_WORDS_ALT = "|".join(_NUMBER_WORDS)

#: A run of number words joined by spaces, hyphens, or "and":
#: "three hundred and twelve". Must start on a number word, never on "and".
_SPELLED_NUMBER = re.compile(
    rf"\b(?:{_WORDS_ALT})(?:[\s-]+(?:{_WORDS_ALT}|and))*\b", re.I
)

#: A digit quantity with a scale word: "1.5 million", "2 thousand". One
#: value, not two — the bare digits must not leak into the asserted set.
_DIGIT_SCALE = re.compile(
    r"\$?\s?(\d[\d,]*(?:\.\d+)?)[\s-]+(hundred|thousand|million|billion)\b", re.I
)


def _words_to_number(tokens: list[str]) -> float | None:
    """Deterministic word-number accumulator. `None` when nothing numeric."""
    total = 0.0
    current = 0.0
    seen = False
    for token in tokens:
        if token == "and":
            continue
        if token in _WORD_UNITS:
            current += _WORD_UNITS[token]
            seen = True
        elif token in _WORD_TENS:
            current += _WORD_TENS[token]
            seen = True
        elif token == "hundred":
            current = max(current, 1) * 100
            seen = True
        elif token in _WORD_SCALES:
            total += max(current, 1) * _WORD_SCALES[token]
            current = 0.0
            seen = True
    return total + current if seen else None


def extract_numbers(text: str) -> set[float]:
    """Every numeric value asserted in a piece of text, normalised.

    Digit forms, spelled-out forms, and digit-plus-scale-word forms all
    reduce to one comparable float. Two deliberate exclusions, so ordinary
    prose does not read as a quantity claim: a standalone "one" or "zero"
    ("one of the first", "no one") is ignored — any longer word sequence
    ("one hundred") still counts — and imprecise plurals ("hundreds of
    students") assert no specific number to check.
    """
    text = text or ""
    found: set[float] = set()
    consumed: list[tuple[int, int]] = []

    for match in _DIGIT_SCALE.finditer(text):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        found.add(value * _WORD_SCALES.get(match.group(2).lower(), 100))
        consumed.append(match.span())

    def overlaps(span: tuple[int, int]) -> bool:
        """Whether `span` was already consumed by a word-scale match.

        Without this, "40 thousand" yields both 40000 (from the scale pass) and
        40 (from the bare-number pass), and the grounding check then looks for a
        40 that the founder never claimed.
        """
        return any(start < span[1] and span[0] < end for start, end in consumed)

    for match in _NUMBER.finditer(text):
        if overlaps(match.span()):
            continue
        raw, suffix = match.group(1), match.group(2)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if suffix in {"K", "k"}:
            value *= 1_000
        elif suffix in {"M", "m"}:
            value *= 1_000_000
        found.add(value)

    for match in _SPELLED_NUMBER.finditer(text):
        if overlaps(match.span()):
            continue
        tokens = [t.lower() for t in re.split(r"[\s-]+", match.group(0)) if t]
        if tokens in (["one"], ["zero"]):
            continue
        value = _words_to_number(tokens)
        if value is not None:
            found.add(float(value))

    return found


def allowed_numbers(
    kb: KnowledgeBase, opportunity: Opportunity | None, extra_text: str = ""
) -> set[float]:
    """Every number the draft is permitted to contain.

    The founder's own sourced facts, their structured traction, and the
    opportunity's own stated figures. Nothing else. This single check catches
    the most damaging hallucination class in the product: writing
    "we have 400 users" when the deck says 40.
    """
    allowed = extract_numbers(kb.text)
    allowed |= {float(v) for v in kb.traction.values()}
    allowed |= extract_numbers(extra_text)
    if opportunity is not None:
        for value in (
            opportunity.award_min,
            opportunity.award_max,
            opportunity.effort_hours_estimate,
        ):
            if value is not None:
                allowed.add(float(value))
        allowed |= extract_numbers(opportunity.description_excerpt)
        for criterion in opportunity.criteria:
            allowed |= extract_numbers(criterion.text)
        if opportunity.deadline:
            allowed |= extract_numbers(opportunity.deadline.isoformat())
    return allowed


# ═════════════════════════════════════════════════════════════════════════════
# Section 11.1 — closed-world check
# ═════════════════════════════════════════════════════════════════════════════

#: A Title Case run ending in a funding-program noun.
_PROGRAM_NAME = re.compile(
    r"\b((?:[A-Z][\w&.'-]*\s+){1,5}"
    r"(?:Program|Grant|Grants|Fellowship|Fund|Prize|Award|Awards|Challenge|Competition|Accelerator|Initiative))\b"
)

#: A Title Case run ending in an organisation noun, or a bare Title Case
#: bigram (a person's name). Crude by design.
_ENTITY_NAME = re.compile(
    r"\b((?:[A-Z][\w&.'-]*\s+){0,4}"
    r"(?:University|College|Institute|Laboratory|Labs?|Center|Centre|Foundation|Incubator|Inc\.?|LLC|Corp\.?|Ventures?))\b"
)

#: Determiners and pronouns that get swept into a Title Case run at the start
#: of a sentence. "The Campus Innovation Fund" and "Campus Innovation Fund"
#: are the same program, and leaving the article on turns a legitimate
#: reference into a spurious block.
_LEADING_NOISE = frozenset(
    {
        "the", "a", "an", "our", "this", "that", "these", "those",
        "my", "your", "its", "their", "we", "i", "it", "they", "and",
        "but", "for", "with", "to", "of", "in", "on", "at",
    }
)


def _normalise(name: str) -> str:
    """Lowercase and collapse to spaced alphanumerics, for name comparison.

    This is why "Acme, Inc." and "acme inc" compare equal. It also means
    punctuation-only differences can never be the reason two names fail to
    match — the checks that use it are about the words.
    """
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _strip_leading_noise(name: str) -> str:
    """Drop leading articles and pronouns from a normalised name.

    "our Fellowship Program" and "the Fellowship Program" both reduce to
    "fellowship program", so a claim cannot dodge a name check by adding a
    determinant in front of it. Only *leading* tokens are stripped: an
    interior "the" is part of the name.
    """
    tokens = _normalise(name).split()
    while tokens and tokens[0] in _LEADING_NOISE:
        tokens.pop(0)
    return " ".join(tokens)


def extract_program_names(text: str) -> set[str]:
    """Named funding programs asserted in a piece of text."""
    names = {_strip_leading_noise(m) for m in _PROGRAM_NAME.findall(text or "")}
    return {n for n in names if n}


def extract_entity_names(text: str) -> set[str]:
    """Named people, institutions and organisations asserted in a text."""
    names = {_strip_leading_noise(m) for m in _ENTITY_NAME.findall(text or "")}
    # A bare organisation noun with nothing in front of it ("the university")
    # carries no identity, so there is nothing to verify.
    return {n for n in names if n and " " in n}


def _known_names(kb: KnowledgeBase, retrieved: list[Opportunity]) -> str:
    """Corpus the closed-world and entity checks are allowed to match against."""
    parts = [kb.text]
    for opp in retrieved:
        parts.extend([opp.title, opp.funder, opp.description_excerpt])
        parts.extend(c.text for c in opp.criteria)
    return "\n".join(parts)


# ═════════════════════════════════════════════════════════════════════════════
# Section 11.9 — one ship gate, fail closed
# ═════════════════════════════════════════════════════════════════════════════

GATE_CHECKS = (
    "BLOCKLIST",
    "PROVENANCE",
    "NUMERIC_WHITELIST",
    "ENTITY_CHECK",
    "CLOSED_WORLD",
    "FORBIDDEN_CLAIMS",
    "AUDITOR_VERDICT",
    "COMPLETENESS",
)


def _force_needs_founder(field: DraftField, reason: str) -> None:
    """Blank the answer as well as the status.

    Leaving the text behind and only flipping the label is how a blocked
    answer ends up pasted into a real form by a founder who trusted the UI.
    """
    field.status = "NEEDS_FOUNDER"
    field.answer = None
    field.provenance = []
    field.audit_verdict = None
    field.audit_note = reason


def ship_gate(
    draft: Draft,
    kb: KnowledgeBase,
    *,
    retrieved: list[Opportunity] | None = None,
    opportunity: Opportunity | None = None,
    audit: AuditReport | None = None,
    required_field_ids: set[str] | None = None,
) -> GateResult:
    """Ordered, fail-closed. First failure stops the chain and is reported.

    Mutates `draft`: sets `draft.gate_result`, sets `draft.status` to READY or
    BLOCKED, and rewrites any blocklisted field to NEEDS_FOUNDER. Callers do
    not get to forget to apply the result.

    The spec sketches this as `ship_gate(draft, kb)`. Checks 5 and 7 cannot
    be implemented from those two arguments alone — the closed-world check
    needs the retrieved set and the auditor check needs the audit report — so
    both are keyword arguments. See DECISIONS.md.
    """
    result = GateResult()
    retrieved = retrieved or []
    try:
        _run_gate(
            result,
            draft,
            kb,
            retrieved=retrieved,
            opportunity=opportunity,
            audit=audit,
            required_field_ids=required_field_ids or set(),
        )
    except Exception as exc:  # noqa: BLE001 — deliberate catch-all
        # An exception in the safety layer must never be interpreted as
        # "passed". This is the whole point of the word "closed".
        result.passed = False
        result.failed_check = "GATE_EXCEPTION"
        result.violations.append(
            GateViolation(
                check="GATE_EXCEPTION",
                field_id=None,
                detail=f"{type(exc).__name__}: {exc}",
                severity="BLOCK",
            )
        )

    draft.gate_result = result
    draft.status = "READY" if result.passed else "BLOCKED"
    return result


def _run_gate(
    result: GateResult,
    draft: Draft,
    kb: KnowledgeBase,
    *,
    retrieved: list[Opportunity],
    opportunity: Opportunity | None,
    audit: AuditReport | None,
    required_field_ids: set[str],
) -> None:
    """Run every ship-gate check in order, appending to `result`.

    Separated from `ship_gate` so the caller can wrap it in one try/except:
    any exception escaping this function becomes a `GATE_EXCEPTION` BLOCK
    rather than a passing gate (Section 11.9).

    Order is load-bearing. The blocklist runs first because it *rewrites*
    fields, and every later check must see the corrected draft. After that the
    checks are ordered cheapest-first and each ends with `failed(...)`, which
    returns early — so `checks_run` records how far the gate got, and a draft
    blocked on provenance is never also reported as blocked on grounding.
    A reader adding a check should append it at the end unless it also
    rewrites fields.
    """
    def block(check: str, field_id: str | None, detail: str) -> None:
        """Record a BLOCK violation. Does not stop the gate on its own — `failed` does."""
        result.violations.append(
            GateViolation(check=check, field_id=field_id, detail=detail, severity="BLOCK")
        )

    def failed(check: str) -> bool:
        """Whether `check` blocked, and if so mark the whole result failed.

        Called immediately after each check group; the caller returns on True.
        Sets `failed_check` to the first blocking check, which is what the UI
        shows as the reason.
        """
        if any(v.check == check and v.severity == "BLOCK" for v in result.violations):
            result.failed_check = check
            result.passed = False
            return True
        return False

    # ── 1. Blocklist ─────────────────────────────────────────────────────
    # A correction, not a failure: rewrite and keep going.
    result.checks_run.append("BLOCKLIST")
    for field in draft.fields:
        category = blocklisted(field.question) or blocklisted(field.field_id)
        if category and field.status != "NEEDS_FOUNDER":
            _force_needs_founder(field, f"blocked field type: {category}")
            result.violations.append(
                GateViolation(
                    check="BLOCKLIST",
                    field_id=field.field_id,
                    detail=f"'{field.question}' is a {category} field — only the founder may answer it",
                    severity="FORCED_NEEDS_FOUNDER",
                )
            )

    generated = [f for f in draft.fields if f.status == "GENERATED"]
    generated_text = "\n".join(f.answer or "" for f in generated)

    # ── 2. Provenance ────────────────────────────────────────────────────
    result.checks_run.append("PROVENANCE")
    for field in generated:
        if not field.provenance:
            block("PROVENANCE", field.field_id, "GENERATED field carries no source span")
    if failed("PROVENANCE"):
        return

    # ── 3. Numeric whitelist ─────────────────────────────────────────────
    result.checks_run.append("NUMERIC_WHITELIST")
    permitted = allowed_numbers(
        kb, opportunity, extra_text="\n".join(f.question for f in draft.fields)
    )
    for field in generated:
        for value in extract_numbers(field.answer or ""):
            if value not in permitted:
                block(
                    "NUMERIC_WHITELIST",
                    field.field_id,
                    f"the number {value:g} does not appear anywhere in the knowledge base "
                    f"or the opportunity's own text",
                )
    if failed("NUMERIC_WHITELIST"):
        return

    # ── 4. Entity check ──────────────────────────────────────────────────
    result.checks_run.append("ENTITY_CHECK")
    corpus = _normalise(_known_names(kb, retrieved))
    for field in generated:
        for entity in extract_entity_names(field.answer or ""):
            if entity and entity not in corpus:
                block(
                    "ENTITY_CHECK",
                    field.field_id,
                    f"named entity '{entity}' is not in the knowledge base or the retrieved set",
                )
    if failed("ENTITY_CHECK"):
        return

    # ── 5. Closed world ──────────────────────────────────────────────────
    result.checks_run.append("CLOSED_WORLD")
    for field in generated:
        for program in extract_program_names(field.answer or ""):
            if program and program not in corpus:
                block(
                    "CLOSED_WORLD",
                    field.field_id,
                    f"names a program '{program}' that was not in this run's retrieved set",
                )
    if failed("CLOSED_WORLD"):
        return

    # ── 6. Forbidden claims ──────────────────────────────────────────────
    result.checks_run.append("FORBIDDEN_CLAIMS")
    kb_text = kb.text
    for field in generated:
        answer = field.answer or ""
        for category, trigger, evidence in FORBIDDEN_CLAIMS:
            if trigger.search(answer) and not evidence_supports_claim(
                answer, trigger, evidence, kb_text
            ):
                if evidence.search(kb_text):
                    detail = (
                        f"asserts a {category} claim whose only knowledge-base "
                        f"match has the opposite polarity — the evidence "
                        f"refutes it rather than supporting it"
                    )
                else:
                    detail = (
                        f"asserts a {category} claim with nothing in the "
                        f"knowledge base to support it"
                    )
                block("FORBIDDEN_CLAIMS", field.field_id, detail)
    if failed("FORBIDDEN_CLAIMS"):
        return

    # ── 7. Auditor verdict ───────────────────────────────────────────────
    # The Drafter loses ties. An UNSUPPORTED field is not repaired, it is
    # handed back to the founder.
    result.checks_run.append("AUDITOR_VERDICT")
    if audit is not None:
        by_field = {a.field_id: a for a in audit.fields}
        for field in generated:
            verdict = by_field.get(field.field_id)
            if verdict is None:
                block("AUDITOR_VERDICT", field.field_id, "generated field was never audited")
            elif verdict.verdict == "UNSUPPORTED":
                block(
                    "AUDITOR_VERDICT",
                    field.field_id,
                    f"auditor found no support for this claim: {verdict.note}",
                )
            elif verdict.verdict == "UNVERIFIABLE":
                block(
                    "AUDITOR_VERDICT",
                    field.field_id,
                    f"auditor could not verify this claim: {verdict.note}",
                )
    if failed("AUDITOR_VERDICT"):
        return

    # ── 8. Completeness ──────────────────────────────────────────────────
    # Answered, or explicitly flagged for the founder. Silently blank is the
    # only unacceptable state.
    result.checks_run.append("COMPLETENESS")
    present = {f.field_id for f in draft.fields}
    for field_id in sorted(required_field_ids - present):
        block("COMPLETENESS", field_id, "required field is missing from the draft entirely")
    for field in draft.fields:
        if field.status != "NEEDS_FOUNDER" and not (field.answer or "").strip():
            block(
                "COMPLETENESS",
                field.field_id,
                f"status is {field.status} but the answer is empty",
            )
    if failed("COMPLETENESS"):
        return

    result.passed = True
    result.failed_check = None
