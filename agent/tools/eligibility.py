"""The deterministic gate. NO LLM CALLS IN THIS FILE.

This is the load-bearing defense of the whole system (Section 10.6). It reads
**structured fields only** — never free text a model summarised — which means
even a completely successful prompt injection inside an opportunity
description cannot change the outcome here. A Python comparison does not
read instructions.

It is also the cheap layer. It takes ~200 opportunities down to ~20 before a
single token is spent.

Three-valued throughout (Section 11.3). `None` on an `EligibilityRules` field
means the source text did not state the rule. That is not permission. It
becomes `UNKNOWN`, and `UNKNOWN` becomes a question for the founder rather
than a silent pass or a silent drop.

Two asymmetries are deliberate, and both lean the same way — toward not
losing money the founder was entitled to:

*   An unstated rule is `UNKNOWN`, not `INELIGIBLE`.
*   An institution restriction we cannot confidently match is `UNKNOWN`, not
    `INELIGIBLE`. "Georgia Tech" and "Georgia Institute of Technology" are
    the same school, and a string comparison that says otherwise would drop a
    real opportunity on a formatting difference.

The cost of that bias is over-triage: some things reach the Assessor that
should not. The Assessor is cheap. A missed $25,000 grant is not.
"""

from __future__ import annotations

import re
from datetime import date

from agent.models import (
    Blocker,
    EligibilityResult,
    FounderProfile,
    Opportunity,
    Rejection,
)

#: Controlled vocabulary for `citizenship`. The seed catalog is ours, so we
#: control both sides of this comparison. A token outside the vocabulary is
#: treated as unmatched rather than silently coerced.
CITIZENSHIP_TOKENS = frozenset(
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

#: Entity types a founder can realistically create inside an application
#: window. Used to decide whether an entity mismatch is a blocker or a wall.
FORMABLE_ENTITIES = frozenset({"llc", "c_corp", "s_corp", "nonprofit"})


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


#: Dropped before comparing institution names — they carry no signal.
_INSTITUTION_NOISE = frozenset({"of", "the", "at", "and", "for", "in"})

#: Shortest token prefix allowed to count as a match. Two characters would
#: let "in" match "institute" and drop the discrimination entirely.
_MIN_PREFIX = 3


def _institution_tokens(name: str) -> list[str]:
    return [t for t in _norm(name).split() if t not in _INSTITUTION_NOISE]


def _token_matches(a: str, b: str) -> bool:
    """Equal, or one is a prefix of the other — "tech" against "technology"."""
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= _MIN_PREFIX and long.startswith(short)


def _institution_matches(required: list[str], founder: str) -> bool:
    """Prefix-token match in both directions.

    Plain containment is not enough: "Georgia Tech" is not a substring of
    "Georgia Institute of Technology", and treating that as a mismatch would
    drop a real opportunity over a name style.

    Every meaningful token on the shorter name must prefix-match a token on
    the longer one. "georgia tech" clears "georgia institute of technology";
    "georgia state" does not.

    Known gap: acronyms. "MIT" will not match "Massachusetts Institute of
    Technology". That failure lands on UNKNOWN, which becomes a question for
    the founder rather than a wrong rejection, so it is survivable.
    TODO: add an acronym expansion table once the seed catalog shows which
    institutions actually appear as initialisms.
    """
    founder_tokens = _institution_tokens(founder)
    if not founder_tokens:
        return False

    for name in required:
        required_tokens = _institution_tokens(name)
        if not required_tokens:
            continue
        short, long = (
            (required_tokens, founder_tokens)
            if len(required_tokens) <= len(founder_tokens)
            else (founder_tokens, required_tokens)
        )
        if all(any(_token_matches(s, l) for l in long) for s in short):
            return True
    return False


def check_opportunity(
    opportunity: Opportunity, profile: FounderProfile, today: date
) -> EligibilityResult:
    """Run every deterministic check against one opportunity.

    Every rejection carries the founder's value and the required value, so
    the run log answers "why did it drop this?" without anyone re-running
    anything.
    """
    rules = opportunity.eligibility
    unknown: list[str] = []
    blockers: list[Blocker] = []

    def reject(check: str, detail: str, mine: str, needed: str) -> EligibilityResult:
        return EligibilityResult(
            opportunity_id=opportunity.id,
            verdict="INELIGIBLE",
            rejection=Rejection(
                opportunity_id=opportunity.id,
                opportunity_title=opportunity.title,
                check=check,
                detail=detail,
                founder_value=mine,
                required_value=needed,
            ),
            unknown_checks=unknown,
            resolvable_blockers=blockers,
        )

    # ── DEADLINE ─────────────────────────────────────────────────────────
    # Date math in Python, never in a model (Section 9, rule 8).
    if opportunity.deadline is not None:
        if opportunity.deadline < today:
            return reject(
                "DEADLINE",
                f"closed {(today - opportunity.deadline).days} days ago",
                today.isoformat(),
                opportunity.deadline.isoformat(),
            )
    elif not opportunity.rolling:
        unknown.append("DEADLINE")

    # ── DEGREE_LEVEL ─────────────────────────────────────────────────────
    if rules.degree_levels is None:
        unknown.append("DEGREE_LEVEL")
    elif profile.degree_level not in rules.degree_levels:
        return reject(
            "DEGREE_LEVEL",
            f"open to {', '.join(rules.degree_levels)} only",
            profile.degree_level,
            "/".join(rules.degree_levels),
        )

    # ── CITIZENSHIP ──────────────────────────────────────────────────────
    if rules.citizenships is None:
        unknown.append("CITIZENSHIP")
    elif profile.citizenship not in rules.citizenships:
        return reject(
            "CITIZENSHIP",
            f"restricted to {', '.join(rules.citizenships)}",
            profile.citizenship,
            "/".join(rules.citizenships),
        )

    # ── GEOGRAPHY ────────────────────────────────────────────────────────
    if rules.geographies is None:
        pass  # no restriction stated is the common case; not worth a question
    elif not profile.geographies:
        unknown.append("GEOGRAPHY")
    elif not ({_norm(g) for g in profile.geographies} & {_norm(g) for g in rules.geographies}):
        return reject(
            "GEOGRAPHY",
            f"restricted to {', '.join(rules.geographies)}",
            ", ".join(profile.geographies),
            "/".join(rules.geographies),
        )

    # ── INSTITUTION ──────────────────────────────────────────────────────
    if rules.institutions:
        if not _institution_matches(rules.institutions, profile.institution):
            # Deliberately UNKNOWN, not INELIGIBLE. A name-formatting
            # difference must not cost the founder a real opportunity.
            unknown.append("INSTITUTION")

    # ── TEAM_SIZE ────────────────────────────────────────────────────────
    if rules.max_team_size is not None and profile.team_size > rules.max_team_size:
        return reject(
            "TEAM_SIZE",
            f"caps teams at {rules.max_team_size}",
            str(profile.team_size),
            f"<={rules.max_team_size}",
        )
    if rules.min_team_size is not None and profile.team_size < rules.min_team_size:
        blockers.append(
            Blocker(
                check="TEAM_SIZE",
                detail=f"requires at least {rules.min_team_size} team members; you have {profile.team_size}",
                remedy=f"add {rules.min_team_size - profile.team_size} teammate(s) before applying",
            )
        )

    # ── ENTITY_TYPE ──────────────────────────────────────────────────────
    if rules.entity_types is None:
        unknown.append("ENTITY_TYPE")
    elif profile.entity_type not in rules.entity_types:
        formable = [e for e in rules.entity_types if e in FORMABLE_ENTITIES]
        if formable:
            blockers.append(
                Blocker(
                    check="ENTITY_TYPE",
                    detail=f"requires {' or '.join(formable)}; you have {profile.entity_type}",
                    remedy=f"form a {formable[0].replace('_', ' ')} before applying",
                )
            )
        else:
            return reject(
                "ENTITY_TYPE",
                f"open to {', '.join(rules.entity_types)} only",
                profile.entity_type,
                "/".join(rules.entity_types),
            )

    # ── FACULTY_PI ───────────────────────────────────────────────────────
    if rules.requires_faculty_pi and not profile.has_faculty_advisor:
        blockers.append(
            Blocker(
                check="FACULTY_PI",
                detail="requires a faculty principal investigator",
                remedy="ask a faculty member to sponsor the application",
            )
        )

    # ── EQUITY ───────────────────────────────────────────────────────────
    # Not in the Section 6 list, but the product is defined as non-dilutive
    # funding and the profile carries `equity_ok`. See DECISIONS.md.
    if rules.takes_equity and not profile.equity_ok:
        return reject(
            "EQUITY",
            "this funder takes equity",
            "non-dilutive only",
            "equity accepted",
        )

    return EligibilityResult(
        opportunity_id=opportunity.id,
        verdict="UNKNOWN" if unknown else "ELIGIBLE",
        unknown_checks=unknown,
        resolvable_blockers=blockers,
    )


def hard_eligibility_filter(
    opportunities: list[Opportunity],
    profile: FounderProfile,
    today: date | None = None,
) -> tuple[list[Opportunity], list[Rejection], dict[str, EligibilityResult]]:
    """Deterministic gate over the whole retrieved set.

    Returns `(survivors, rejections, results_by_id)`. Section 6 specifies the
    first two; the third carries the UNKNOWN checks and resolvable blockers
    forward to the Assessor, which otherwise has to re-derive them from text.

    Survivors are ELIGIBLE **and** UNKNOWN. Dropping UNKNOWN here would make
    the three-valued logic decorative.
    """
    today = today or date.today()
    survivors: list[Opportunity] = []
    rejections: list[Rejection] = []
    results: dict[str, EligibilityResult] = {}

    for opportunity in opportunities:
        result = check_opportunity(opportunity, profile, today)
        results[opportunity.id] = result
        if result.verdict == "INELIGIBLE":
            assert result.rejection is not None
            rejections.append(result.rejection)
        else:
            survivors.append(opportunity)

    return survivors, rejections, results
