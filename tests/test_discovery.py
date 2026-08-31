"""Discovery. Runs offline against fixtures recorded from real API calls."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from agent.models import SourceName
from agent.tools.discovery import (
    GrantsGovSource,
    SeedCatalog,
    SourceError,
    _parse_close_date,
    _parse_money,
    _parse_response_date,
    discover_opportunities,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 22)


@pytest.fixture
def search_hits() -> list[dict]:
    """Canned Grants.gov search hits with the field names the real API returns."""
    return json.loads((FIXTURES / "grants_gov_search2.json").read_text())["data"]["oppHits"]


@pytest.fixture
def detail() -> dict:
    """A canned `fetchOpportunity` payload."""
    return json.loads((FIXTURES / "grants_gov_fetchOpportunity.json").read_text())["data"]


# ── Field parsing, against the formats the live API actually returns ────────


def test_close_date_is_month_day_year():
    assert _parse_close_date("08/18/2027") == date(2027, 8, 18)


def test_empty_close_date_is_none_not_an_error():
    assert _parse_close_date("") is None
    assert _parse_close_date(None) is None


def test_unparseable_close_date_degrades_to_none():
    assert _parse_close_date("sometime next spring") is None


def test_response_date_str_format():
    assert _parse_response_date("2027-08-18-00-00-00") == date(2027, 8, 18)


@pytest.mark.parametrize(
    "raw,expected",
    [("500000", 500_000), ("5000", 5_000), ("", None), ("0", None), (None, None), ("$1,200", 1_200)],
)
def test_money_parsing(raw, expected):
    assert _parse_money(raw) == expected


# ── Mapping ─────────────────────────────────────────────────────────────────


def test_mapping_uses_only_verified_response_fields(search_hits, detail):
    opportunity = GrantsGovSource.to_opportunity(search_hits[0], detail)

    assert opportunity.id.startswith("grants_gov:")
    assert opportunity.source == "grants_gov"
    assert opportunity.source_url.startswith("https://www.grants.gov/search-results-detail/")
    assert opportunity.title
    assert opportunity.funder


def test_unrepresentable_grants_gov_eligibility_stays_unknown(search_hits, detail):
    """An 'Others' category must not be narrowed from its prose."""
    opportunity = GrantsGovSource.to_opportunity(search_hits[0], detail)
    rules = opportunity.eligibility

    assert rules.degree_levels is None
    assert rules.citizenships is None
    assert rules.entity_types is None


def test_structured_small_business_category_maps_to_supported_entity_types(search_hits):
    detail = {
        "synopsis": {
            "applicantTypes": [{"id": "23", "description": "Small businesses"}]
        }
    }

    opportunity = GrantsGovSource.to_opportunity(search_hits[0], detail)

    assert opportunity.eligibility.entity_types == ["llc", "c_corp", "s_corp"]


def test_mixed_grants_categories_stay_unknown_when_one_is_not_representable(search_hits):
    detail = {
        "synopsis": {
            "applicantTypes": [
                {"id": "23", "description": "Small businesses"},
                {"id": "25", "description": "Others (see additional information)"},
            ]
        }
    }

    opportunity = GrantsGovSource.to_opportunity(search_hits[0], detail)

    assert opportunity.eligibility.entity_types is None


def test_eligibility_prose_is_kept_verbatim_as_a_criterion(search_hits, detail):
    opportunity = GrantsGovSource.to_opportunity(search_hits[0], detail)

    assert opportunity.criteria, "the eligibility text must survive as a quotable span"
    assert any("applicantEligibilityDesc" in c.source_doc for c in opportunity.criteria)


def test_html_is_stripped_from_mapped_text(search_hits, detail):
    opportunity = GrantsGovSource.to_opportunity(search_hits[0], detail)

    assert "<p>" not in opportunity.description_excerpt
    assert "&amp;" not in opportunity.title
    assert "&nbsp;" not in "".join(c.text for c in opportunity.criteria)


def test_mapping_without_detail_still_produces_a_valid_opportunity(search_hits):
    opportunity = GrantsGovSource.to_opportunity(search_hits[0], {})
    assert opportunity.award_min is None
    assert opportunity.description_excerpt == ""


# ── Seed catalog ────────────────────────────────────────────────────────────


def write_seed(tmp_path: Path, rows: list[dict]) -> Path:
    """Write a seed catalog file to `tmp_path` and return its path."""
    path = tmp_path / "opportunities.seed.json"
    path.write_text(json.dumps(rows))
    return path


def seed_row(**overrides) -> dict:
    """One catalog row as the JSON the seed file holds, not as an `Opportunity`.

    Staying in JSON is the point: these tests cover the file-to-model
    boundary, and building an `Opportunity` directly would skip it.
    """
    row = {
        "id": "seed_1",
        "title": "[DEMO] Student Innovation Fund",
        "funder": "[DEMO] Example University",
        "source_url": "https://example.invalid/demo",
        "verified": True,
        "verified_at": "2026-08-22T00:00:00Z",
    }
    row.update(overrides)
    return row


def test_unverified_rows_are_excluded_from_runs(tmp_path):
    path = write_seed(
        tmp_path,
        [seed_row(id="good"), seed_row(id="unverified", verified=False, verified_at=None)],
    )

    found = SeedCatalog(path).fetch(NOW)

    assert [o.id for o in found] == ["good"]


def test_unverified_rows_can_be_opted_into_explicitly(tmp_path):
    path = write_seed(tmp_path, [seed_row(id="unverified", verified=False, verified_at=None)])

    assert len(SeedCatalog(path, allow_unverified=True).fetch(NOW)) == 1


def test_missing_seed_catalog_raises_rather_than_returning_empty(tmp_path):
    with pytest.raises(SourceError, match="not found"):
        SeedCatalog(tmp_path / "nope.json").fetch(NOW)


def test_malformed_seed_catalog_raises(tmp_path):
    path = tmp_path / "opportunities.seed.json"
    path.write_text("{not json")

    with pytest.raises(SourceError, match="valid JSON"):
        SeedCatalog(path).fetch(NOW)


# ── Whole-sweep behaviour ───────────────────────────────────────────────────


class FakeSource:
    """A source returning a fixed list, or raising if given an exception."""

    def __init__(self, name: SourceName, result):
        """Pass opportunities to return them; pass an exception to raise it from `fetch`."""
        self.name = name
        self._result = result

    def fetch(self, since):
        """Return the canned list, or raise the canned exception."""
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def test_a_dead_source_is_reported_and_the_run_continues(tmp_path):
    path = write_seed(tmp_path, [seed_row(id="alive")])
    sources = [
        FakeSource("grants_gov", SourceError("connection timed out")),
        SeedCatalog(path),
    ]

    found, failures = discover_opportunities(sources, NOW)

    assert [o.id for o in found] == ["alive"]
    assert len(failures) == 1
    assert failures[0].source == "grants_gov"
    assert "timed out" in failures[0].detail


def test_failures_are_never_silently_swallowed(tmp_path):
    sources = [FakeSource("browser", RuntimeError("chrome crashed"))]

    found, failures = discover_opportunities(sources, NOW)

    assert found == []
    assert failures[0].detail.startswith("RuntimeError")


def test_curated_seed_row_wins_over_a_live_duplicate(tmp_path):
    path = write_seed(tmp_path, [seed_row(id="dupe", title="[DEMO] Curated title")])
    from tests.factories import opportunity

    sources = [SeedCatalog(path), FakeSource("grants_gov", [opportunity(id="dupe", title="live title")])]

    found, _ = discover_opportunities(sources, NOW)

    assert len(found) == 1
    assert found[0].title == "[DEMO] Curated title"


def test_curation_metadata_does_not_break_the_loader(tmp_path):
    """Rows carry notes from humans and from the verifier. Neither is a field."""
    path = write_seed(
        tmp_path,
        [
            seed_row(
                id="annotated",
                _curation_note="a human wrote this",
                verification_note="HTTP 200, title terms present",
            )
        ],
    )

    assert [o.id for o in SeedCatalog(path).fetch(NOW)] == ["annotated"]


def test_a_typo_in_a_curated_row_still_fails_loudly(tmp_path):
    path = write_seed(tmp_path, [seed_row(id="typo", award_maximum=5000)])

    with pytest.raises(Exception, match="award_maximum"):
        SeedCatalog(path).fetch(NOW)


def test_the_real_seed_catalog_parses():
    """Whatever scripts/verify_seed.py last wrote must be loadable."""
    real = Path(__file__).parent.parent / "data" / "opportunities.seed.json"
    if not real.exists():
        pytest.skip("seed catalog has not been generated yet")
    SeedCatalog(real, allow_unverified=True).fetch(NOW)
