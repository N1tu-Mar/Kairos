"""The targets, written by a human who opened each page.

Every row here was reached in a browser before it was written down. That is
the same rule the seed catalog runs under (`data/README.md`): a plausible URL
someone typed from memory is worse than a shorter list.

## Two tiers, and why the second one exists

The brief says **Rutgers-owned domains only**. The supplied target list also
contains four pages that are not on a Rutgers domain — NJIT, Stevens, Devpost
and Campus Labs — and every one of them is there for a reason a Rutgers
founder cares about: NJIT's competition is open to Northern NJ students,
Stevens' is the instructive negative, Devpost hosts a Rutgers event, and
Campus Labs hosts Rutgers' own student organisation directory.

Rather than quietly widening the rule, the two ideas are kept apart:

*   `RUTGERS` — Rutgers-owned. These are the only domains link discovery is
    ever allowed to expand into.
*   `PROVIDED_EXTERNAL` — a specific page the operator named. Fetched
    exactly once, at exactly that URL, never crawled, and flagged in the
    output so a reviewer sees immediately that it is off-domain.

Nothing else is reachable. There is no code path that follows a link off a
Rutgers host.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Tier = Literal["RUTGERS", "PROVIDED_EXTERNAL"]

#: Link discovery never leaves this set, and only runs when asked for.
RUTGERS_DOMAINS = frozenset(
    {
        "rutgers.edu",
        "idea.rutgers.edu",
        "myrbs.business.rutgers.edu",
        "business.rutgers.edu",
        "innovate.njaes.rutgers.edu",
        "njaes.rutgers.edu",
        "research.rutgers.edu",
        "sccinnovation.rutgers.edu",
    }
)


@dataclass(frozen=True)
class Target:
    """One page to fetch, and what a human already knew about it."""

    key: str
    title: str
    organization: str
    url: str
    tier: Tier
    priority: int = 2
    #: Set only where a static fetch has already been shown to return a
    #: JavaScript shell. Nothing is rendered speculatively.
    requires_js: bool = False
    #: Written before the scrape, kept in the output. Context a parser cannot
    #: derive: how a Rutgers founder actually reaches this money.
    operator_note: str = ""
    #: Recorded when there is no stable application page to fetch. The row
    #: still appears in the review file, with every field UNKNOWN, so a real
    #: opportunity is not lost just because it has no URL yet.
    no_stable_url: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)


TARGETS: tuple[Target, ...] = (
    Target(
        key="rutgers_scarletpitch",
        title="ScarletPitch",
        organization="Rutgers Innovation, Design, and Entrepreneurship Academy (IDEA)",
        url="https://idea.rutgers.edu/programs/scarletpitch",
        tier="RUTGERS",
        priority=1,
        operator_note=(
            "The reference example. Rutgers-New Brunswick undergraduate and graduate "
            "students, teams of 1-5. Also the qualifying route into UPitchNJ and the "
            "Hult Prize, so it is worth more than its own prize money."
        ),
        tags=("pitch competition", "cash prize", "undergraduate"),
    ),
    Target(
        key="rutgers_rbs_business_plan",
        title="RBS Business Plan Competition",
        organization="Rutgers Business School",
        url="https://myrbs.business.rutgers.edu/case-competitions/business-plan",
        tier="RUTGERS",
        priority=1,
        operator_note=(
            "The conditional-eligibility case. Open to Rutgers students generally, but "
            "the venture's leadership roles carry a separate RBS senior / MBA / recent "
            "alumni requirement. Read the caveats before deciding this is applicable."
        ),
        tags=("business plan competition", "cash prize", "conditional eligibility"),
    ),
    Target(
        key="rutgers_upitchnj",
        title="UPitchNJ",
        organization="Rutgers Entrepreneurship Coalition",
        url="https://innovate.njaes.rutgers.edu/upitchnj-ru-2021/",
        tier="RUTGERS",
        priority=1,
        operator_note=(
            "Statewide, and NOT directly applicable. A Rutgers undergraduate reaches "
            "UPitchNJ by winning ScarletPitch, not by applying. Treated as an indirect "
            "opportunity so it is never surfaced as something to go and apply for."
        ),
        tags=("pitch competition", "statewide", "indirect access"),
    ),
    Target(
        key="rutgers_entrepreneurial_society",
        title="Rutgers Shark Tank (Rutgers Entrepreneurial Society)",
        organization="Rutgers Entrepreneurial Society",
        url="https://rutgers.campuslabs.com/engage/organization/RES",
        tier="PROVIDED_EXTERNAL",
        priority=1,
        requires_js=True,
        operator_note=(
            "Rutgers' student organisation directory is hosted on campuslabs.com, so "
            "the page is off-domain but the content is Rutgers'. It renders through "
            "JavaScript, which is the single reason a browser renderer exists in this "
            "pipeline at all."
        ),
        tags=("pitch competition", "student organisation"),
    ),
    Target(
        key="rutgers_techstart",
        title="Rutgers TechStart Innovation Challenge",
        organization="Rutgers Business School",
        url="",
        tier="RUTGERS",
        priority=1,
        no_stable_url=True,
        operator_note=(
            "Documented by Rutgers Business School as a real initiative, but with no "
            "stable standalone application page found at the time of writing. Recorded "
            "here with every field UNKNOWN rather than populated from secondhand "
            "descriptions. A human must find and add the application URL."
        ),
        tags=("innovation challenge", "needs url"),
    ),
    Target(
        key="njit_new_business_model",
        title="New Business Model Competition",
        organization="New Jersey Innovation Acceleration Center (NJIT)",
        url="https://research.njit.edu/njiac/new-business-model-competition",
        tier="PROVIDED_EXTERNAL",
        priority=1,
        operator_note=(
            "Off-domain but genuinely open to a Rutgers founder: the competition takes "
            "current students at Northern NJ colleges and universities, not only NJIT "
            "students. Verify the current year's scope in the evidence spans."
        ),
        tags=("business model competition", "open beyond host", "fellowship"),
    ),
    Target(
        key="rutgers_mtc_code_for_impact",
        title="Rutgers MTC Code for Impact Hackathon",
        organization="Rutgers Master of Technology Commercialization / MTC",
        url="https://mtc-code-for-impact-hackathon.devpost.com/",
        tier="PROVIDED_EXTERNAL",
        priority=2,
        operator_note=(
            "A Rutgers-hosted event on a third-party host. Included as the case where "
            "discovery should find something and funding triage should probably reject "
            "it: the listed prizes were non-cash. Confirm against the award evidence."
        ),
        tags=("hackathon", "possibly non-cash"),
    ),
    Target(
        key="stevens_ansary",
        title="Ansary Entrepreneurship Competition",
        organization="Stevens Institute of Technology",
        url="https://www.stevens.edu/ansary-entrepreneurship-competition",
        tier="PROVIDED_EXTERNAL",
        priority=2,
        operator_note=(
            "The hard negative. Real money, but the competition is built around Stevens "
            "senior design teams. Kept in the review file precisely so the reasoning "
            "for rejecting it is visible rather than assumed."
        ),
        tags=("competition", "host-restricted", "likely ineligible"),
    ),
)


def by_key(key: str) -> Target | None:
    return next((t for t in TARGETS if t.key == key), None)


def fetchable(priority_max: int = 2) -> list[Target]:
    """Targets with a URL to fetch, at or above the given priority."""
    return [t for t in TARGETS if t.url and t.priority <= priority_max]


def is_rutgers_domain(url: str) -> bool:
    """Whether link discovery is permitted to expand into this URL."""
    from urllib.parse import urlsplit

    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return host in RUTGERS_DOMAINS or host.endswith(".rutgers.edu")
