"""Test fixtures built in code. Obviously synthetic, obviously labelled.

Every opportunity here has `[DEMO]` in its title and `verified=False`, so a
fixture can never be mistaken for a curated real row if it leaks into a
data file or a screenshot.
"""

from __future__ import annotations

from datetime import date, timedelta

from agent.models import (
    ApplicationField,
    ApplicationForm,
    Draft,
    DraftField,
    EligibilityRules,
    FounderProfile,
    KnowledgeBase,
    KnowledgeChunk,
    Opportunity,
    SourceSpan,
)

TODAY = date(2026, 8, 22)


def profile(**overrides) -> FounderProfile:
    """A founder profile with sane defaults. Override one field by keyword.

    The defaults are chosen to be *eligible* for `opportunity()` so a test
    asserting a rejection has to state which field it changed.
    """
    base = dict(
        founder_id="founder_demo",
        degree_level="undergrad",
        institution="Georgia Institute of Technology",
        citizenship="us_citizen",
        entity_type="none",
        team_size=2,
        stage="mvp",
        traction={"users": 40, "interviews": 12},
        funding_range=(2_000, 50_000),
        equity_ok=False,
        has_faculty_advisor=False,
        max_application_hours=8,
        geographies=["GA", "US"],
        knowledge_base=[],
    )
    base.update(overrides)
    return FounderProfile(**base)


def opportunity(**overrides) -> Opportunity:
    """A demo opportunity.

    `eligibility=` takes an `EligibilityRules`; everything else is a plain
    keyword override.

    `verified=False` and the `[DEMO]` title mean this row would be excluded
    from a real run by `SeedCatalog` — deliberate, so a fixture cannot become
    a live catalog entry by being copied into a data file.
    """
    rules = overrides.pop("eligibility", None) or EligibilityRules()
    base = dict(
        id="demo_opp_1",
        title="[DEMO] Student Innovation Fund",
        funder="[DEMO] Example University",
        source="seed",
        source_url="https://example.invalid/demo",
        award_min=5_000,
        award_max=15_000,
        deadline=TODAY + timedelta(days=45),
        rolling=False,
        effort_hours_estimate=6.0,
        description_excerpt="Open to enrolled students building early-stage ventures.",
        verified=False,
    )
    base.update(overrides)
    return Opportunity(eligibility=rules, **base)


def kb(*texts: str, traction: dict[str, float] | None = None) -> KnowledgeBase:
    """A knowledge base from raw strings, one chunk each, ids `c0`, `c1`, ….

    The ids are positional, so `span("c1")` refers to the second string
    passed here — that coupling is what lets provenance tests stay short.
    """
    return KnowledgeBase(
        founder_id="founder_demo",
        chunks=[
            KnowledgeChunk(chunk_id=f"c{i}", text=t, source=f"pitch_deck.pdf p.{i + 1}")
            for i, t in enumerate(texts)
        ],
        traction=traction or {},
    )


def span(chunk_id: str = "c0", text: str = "supporting text") -> SourceSpan:
    """A source span pointing at chunk `c0` unless told otherwise."""
    return SourceSpan(chunk_id=chunk_id, source="pitch_deck.pdf p.1", text=text)


def draft(*fields: DraftField, **overrides) -> Draft:
    """A draft wrapping the given fields, with demo ids."""
    base = dict(
        draft_id="draft_1",
        founder_id="founder_demo",
        opportunity_id="demo_opp_1",
        form_name="[DEMO] form",
    )
    base.update(overrides)
    return Draft(fields=list(fields), **base)


def generated(
    field_id: str,
    answer: str,
    question: str = "Describe your traction to date.",
    provenance=None,
) -> DraftField:
    """A GENERATED field that would pass the gate.

    Carries provenance, a model id, a prompt version and a SUPPORTED audit.

    The starting point for negative tests — strip one of those attributes and
    assert the gate blocks. `provenance=[]` is the interesting override.
    """
    return DraftField(
        field_id=field_id,
        question=question,
        answer=answer,
        status="GENERATED",
        provenance=[span()] if provenance is None else provenance,
        model_id="[DEMO]model",
        prompt_version="deadbeef",
        audit_verdict="SUPPORTED",
    )


def form(*fields: ApplicationField) -> ApplicationForm:
    """An application form wrapping the given fields, with demo ids."""
    return ApplicationForm(
        opportunity_id="demo_opp_1",
        name="[DEMO] form",
        source_url="https://example.invalid/demo",
        fields=list(fields),
    )


def budget(**overrides):
    """A `RunBudget` for tests that call a sub-agent.

    Built from `settings()` so it picks up the autouse `fake_env` fixture's
    tmp state directory — the ledger is a real file and must not be the
    developer's own. `budget` is a required argument on every sub-agent
    entry point precisely so a call site cannot forget to charge, which
    means the fakes need one too.
    """
    from agent.budget import RunBudget
    from agent.config import settings

    b = RunBudget.from_settings(settings())
    for key, value in overrides.items():
        setattr(b, key, value)
    return b
