"""Eligibility extraction — perception, kept apart from decision.

Eligibility arrives as prose: *"Open to undergraduate and graduate students
enrolled at a US institution, including but not limited to engineering
majors."* The deterministic filter needs structured fields. Something has to
cross that gap, and the crossing is the most dangerous step in the pipeline:
an extraction error becomes a confident eligibility verdict, and a confident
wrong verdict either drops a real opportunity or sends a founder to spend
four hours on one they cannot win.

So the boundary is built with one rule:

    **A structured field is populated only when a verbatim span of the source
    text supports it, and that span is re-found in the source before the
    field is allowed to exist.**

The three stages are deliberately separate objects, and only the middle one
may involve a model:

    source prose
        │
        ▼  perception — may be a model, may be a regex. Untrusted.
    EligibilityExtraction (claims, each carrying a quoted span)
        │
        ▼  verification — pure Python. No model. Re-finds every span in the
        │  source, applies the controlled vocabulary, resolves conflicts.
    VerifiedExtraction (claims that survived + why the others died)
        │
        ▼  projection — pure Python, total, no judgment left in it.
    EligibilityRules  →  the existing deterministic filter

The verification stage is where the safety lives, and it is why the
perception stage is allowed to be a model at all. A model that hallucinates
a rule must also hallucinate a quote that appears verbatim in the page, and
if it does that, the quote is on the page.

## What "UNKNOWN" means here

The same thing it means everywhere else in this codebase: the source did not
state it. Five situations all collapse to UNKNOWN rather than to a guess:

*   The extractor said nothing about the field.
*   The extractor supplied a span that is not in the source (fabricated, or
    paraphrased — a paraphrase is a fabrication with better manners).
*   The span is in the source but the value is outside the controlled
    vocabulary.
*   The span is negated, or sits inside an exception clause, and the
    extractor read it as a permission.
*   Two claims about the same field disagree, and the source does not say
    which section wins.

Every one of those is recorded in `VerifiedExtraction.dropped` with the
reason, so a reviewer can tell "the page was silent" from "the extractor
lied" — which are the same output and very different problems.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.models import EligibilityRules

#: Fields this boundary is allowed to populate. Anything else an extractor
#: invents is dropped — the vocabulary is ours, not the page's.
EXTRACTABLE_FIELDS = (
    "degree_levels",
    "citizenships",
    "entity_types",
    "institutions",
    "geographies",
    "min_team_size",
    "max_team_size",
    "requires_faculty_pi",
    "takes_equity",
)

DEGREE_LEVELS = frozenset({"undergrad", "masters", "phd", "postdoc"})
ENTITY_TYPES = frozenset({"none", "llc", "c_corp", "s_corp", "nonprofit"})
CITIZENSHIPS = frozenset(
    {
        "us_citizen",
        "us_permanent_resident",
        "us_national",
        "daca",
        "f1_visa",
        "j1_visa",
        "other_international",
    }
)

#: Negation markers. A span that denies the rule cannot support it — the
#: sentence that most clearly refutes a claim is usually the one containing
#: its keywords, which is the exact bug the golden set found in the grounding
#: layer (DECISIONS.md, 2026-08-26).
_NEGATION = re.compile(
    r"\b(not|no|never|ineligible|excluded|exclude[sd]?|may not|cannot|"
    r"are not eligible|is not eligible|do not qualify|does not qualify)\b",
    re.I,
)

#: Exception markers. "Open to all students except undergraduates" states a
#: restriction the naive read inverts.
_EXCEPTION = re.compile(r"\b(except|excluding|other than|apart from|unless)\b", re.I)

#: Non-exhaustive list markers. "including but not limited to" means the list
#: is illustrative, so treating it as the complete set of allowed values
#: manufactures a restriction the page never stated.
_NON_EXHAUSTIVE = re.compile(
    r"\b(including but not limited to|including, but not limited to|"
    r"such as|for example|e\.g\.|among others|and others)\b",
    re.I,
)


def normalize(text: str) -> str:
    """Lowercase, every run of non-alphanumerics collapsed to one space.

    Span matching has to survive curly quotes, non-breaking spaces and line
    wrapping without degrading into keyword search, so punctuation is
    flattened and word order is not.
    """
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


# ── Stage 1: what an extractor claims ────────────────────────────────────────


class EligibilityClaim(BaseModel):
    """One structured fact, plus the span an extractor says supports it.

    `evidence` must be copied from the source, not written about it. That is
    checkable, and `verify()` checks it.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="One of EXTRACTABLE_FIELDS.")
    value: Any = Field(description="The structured value being claimed.")
    evidence: str = Field(description="Verbatim span copied from the source text.")
    source_ref: str = Field(
        default="", description='Where the span came from, e.g. "grants.gov/1234#eligibility".'
    )


class EligibilityExtraction(BaseModel):
    """A perception layer's whole output. Untrusted until verified.

    This is the model returned by `structured_call`, so a sub-agent that
    cannot produce it abstains rather than emitting freeform prose
    (Section 9, rule 9).
    """

    model_config = ConfigDict(extra="forbid")

    claims: list[EligibilityClaim] = Field(default_factory=list)
    #: Fields the extractor looked for and did not find. Advisory only —
    #: verification derives the real UNKNOWN set, because an extractor that
    #: forgets to list a field must not thereby claim it.
    unstated: list[str] = Field(default_factory=list)


# ── Stage 2: verification, pure Python ───────────────────────────────────────

DropReason = Literal[
    "UNKNOWN_FIELD",
    "SPAN_NOT_IN_SOURCE",
    "VALUE_OUT_OF_VOCABULARY",
    "NEGATED_SPAN",
    "EXCEPTION_CLAUSE",
    "NON_EXHAUSTIVE_LIST",
    "CONFLICTING_CLAIMS",
]


class DroppedClaim(BaseModel):
    """A claim the verifier refused, with the reason it refused it.

    Kept rather than discarded so the drop is auditable: `evidence` is the
    span the extractor cited, and `reason` is the closed vocabulary a reader
    can count by. Frozen — a drop is a finding, not a working value.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    value: Any
    evidence: str
    reason: DropReason
    detail: str


class VerifiedExtraction(BaseModel):
    """What survived, what did not, and why.

    `unknown_fields` is derived, never taken from the extractor: any field
    without a surviving claim is UNKNOWN, whatever the extractor said about
    its own coverage.
    """

    model_config = ConfigDict(extra="forbid")

    claims: list[EligibilityClaim] = Field(default_factory=list)
    dropped: list[DroppedClaim] = Field(default_factory=list)

    @property
    def unknown_fields(self) -> list[str]:
        """Extractable fields with no surviving claim.

        Derived from `claims` each time rather than stored, so a field whose only
        claim was dropped is UNKNOWN automatically. That is the whole point: the
        extractor does not get to report its own coverage.
        """
        populated = {c.field for c in self.claims}
        return [f for f in EXTRACTABLE_FIELDS if f not in populated]

    def value(self, field: str) -> Any:
        """The first surviving claim's value for `field`, or None.

        None is genuinely ambiguous here — it means either "no claim survived" or
        "the claim's value is None". Callers that need to tell those apart check
        `unknown_fields` instead.
        """
        for claim in self.claims:
            if claim.field == field:
                return claim.value
        return None

    def evidence_for(self, field: str) -> str | None:
        """The evidence span behind `field`'s value, or None if nothing survived.

        Paired with `value`: both scan for the first claim on the field, so they
        always describe the same claim.
        """
        for claim in self.claims:
            if claim.field == field:
                return claim.evidence
        return None


def _vocabulary_ok(field: str, value: Any) -> tuple[bool, str]:
    """Is `value` shaped and worded the way this field requires?"""
    if field in ("min_team_size", "max_team_size"):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return False, f"{field} must be a positive integer, got {value!r}"
        return True, ""
    if field in ("requires_faculty_pi", "takes_equity"):
        if not isinstance(value, bool):
            return False, f"{field} must be a boolean, got {value!r}"
        return True, ""
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        return False, f"{field} must be a non-empty list of strings, got {value!r}"
    allowed = {
        "degree_levels": DEGREE_LEVELS,
        "entity_types": ENTITY_TYPES,
        "citizenships": CITIZENSHIPS,
    }.get(field)
    if allowed is not None:
        unknown = [v for v in value if v not in allowed]
        if unknown:
            return False, f"{field} values outside the controlled vocabulary: {unknown}"
    return True, ""


#: Fields whose meaning is a *permission* — a negated or excepted span cannot
#: support them. `takes_equity` is excluded on purpose: "we do not take
#: equity" is a negated span that legitimately supports `takes_equity=False`,
#: and the polarity is carried by the value rather than by the sentence.
_PERMISSION_FIELDS = frozenset(
    {"degree_levels", "citizenships", "entity_types", "institutions", "geographies"}
)

#: Fields where an illustrative list must not become a closed set.
_CLOSED_SET_FIELDS = frozenset({"degree_levels", "entity_types", "institutions", "geographies"})


def verify(
    extraction: EligibilityExtraction, source_text: str
) -> VerifiedExtraction:
    """Re-check every claim against the source. No model, no network.

    A claim survives only if all of these hold:

    1.  Its field is one this boundary may populate.
    2.  Its evidence span appears in the source text, normalised.
    3.  Its value fits the field's shape and controlled vocabulary.
    4.  The span is not a negation or an exception clause being read as a
        permission.
    5.  The span does not mark its own list as illustrative while the claim
        treats that list as the complete set of allowed values.
    6.  No other surviving claim about the same field disagrees with it.
    """
    source = normalize(source_text)
    survivors: list[EligibilityClaim] = []
    dropped: list[DroppedClaim] = []

    def kill(claim: EligibilityClaim, reason: DropReason, detail: str) -> None:
        """Record a claim as dropped. The caller must `continue` — this does not skip it."""
        dropped.append(
            DroppedClaim(
                field=claim.field,
                value=claim.value,
                evidence=claim.evidence,
                reason=reason,
                detail=detail,
            )
        )

    for claim in extraction.claims:
        if claim.field not in EXTRACTABLE_FIELDS:
            kill(claim, "UNKNOWN_FIELD", f"{claim.field!r} is not an extractable field")
            continue

        span = normalize(claim.evidence)
        if not span or span not in source:
            # The single most important check in this file. A paraphrase and
            # a fabrication fail it identically, which is correct: both are
            # text the page does not contain.
            kill(
                claim,
                "SPAN_NOT_IN_SOURCE",
                "the quoted span does not appear in the source text",
            )
            continue

        ok, why = _vocabulary_ok(claim.field, claim.value)
        if not ok:
            kill(claim, "VALUE_OUT_OF_VOCABULARY", why)
            continue

        if claim.field in _CLOSED_SET_FIELDS and _NON_EXHAUSTIVE.search(claim.evidence):
            kill(
                claim,
                "NON_EXHAUSTIVE_LIST",
                "the span marks its list as illustrative, so it cannot establish "
                "the complete set of permitted values",
            )
            continue

        # The non-exhaustive markers are stripped before the negation scan:
        # "including but not limited to" contains "not" and would otherwise
        # read as a denial. Checked in that order, and stripped as well,
        # because a span can be illustrative for one field and negated for
        # another.
        polarity_text = _NON_EXHAUSTIVE.sub(" ", claim.evidence)

        if claim.field in _PERMISSION_FIELDS and _NEGATION.search(polarity_text):
            kill(
                claim,
                "NEGATED_SPAN",
                "the span denies the rule it is offered as evidence for",
            )
            continue

        if claim.field in _PERMISSION_FIELDS and _EXCEPTION.search(polarity_text):
            kill(
                claim,
                "EXCEPTION_CLAUSE",
                "the span carves out an exception; which side the value falls on "
                "is not decidable from it",
            )
            continue

        survivors.append(claim)

    # 6. Conflicts. Two surviving claims about one field that disagree mean
    #    the page says two things, or the extractor read one section twice.
    #    Either way the honest answer is UNKNOWN — picking a winner here is
    #    exactly how an extraction error becomes a confident verdict.
    by_field: dict[str, list[EligibilityClaim]] = {}
    for claim in survivors:
        by_field.setdefault(claim.field, []).append(claim)

    final: list[EligibilityClaim] = []
    for field, claims in by_field.items():
        values = {_canonical(c.value) for c in claims}
        if len(values) > 1:
            for claim in claims:
                kill(
                    claim,
                    "CONFLICTING_CLAIMS",
                    f"{len(claims)} claims about {field} disagree; the source does "
                    f"not say which section governs",
                )
            continue
        final.append(claims[0])

    return VerifiedExtraction(claims=final, dropped=dropped)


def _canonical(value: Any) -> Any:
    """Order-insensitive comparison key. ["a","b"] and ["b","a"] agree."""
    if isinstance(value, list):
        return tuple(sorted(str(v) for v in value))
    return value


# ── Stage 3: projection into the deterministic filter's input ────────────────


def to_eligibility_rules(verified: VerifiedExtraction) -> EligibilityRules:
    """Total, mechanical, no judgment left. Every unfilled field stays None.

    `None` reaches the filter as UNKNOWN, which becomes a founder-facing
    question rather than a silent pass or a silent drop (Section 11.3).
    """
    return EligibilityRules(
        degree_levels=verified.value("degree_levels"),
        citizenships=verified.value("citizenships"),
        entity_types=verified.value("entity_types"),
        institutions=verified.value("institutions"),
        geographies=verified.value("geographies"),
        min_team_size=verified.value("min_team_size"),
        max_team_size=verified.value("max_team_size"),
        requires_faculty_pi=verified.value("requires_faculty_pi"),
        takes_equity=verified.value("takes_equity"),
    )


def extract_and_verify(
    extraction: EligibilityExtraction, source_text: str
) -> tuple[EligibilityRules, VerifiedExtraction]:
    """The whole boundary in one call: verify, then project.

    Returns the rules *and* the verification record, because the record is
    what a reviewer reads when a field came back UNKNOWN and they want to
    know whether the page was silent or the extractor was wrong.
    """
    verified = verify(extraction, source_text)
    return to_eligibility_rules(verified), verified
