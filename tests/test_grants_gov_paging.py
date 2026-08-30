"""GrantsGovSource pagination, date filtering, dedup, and failure reporting.

All offline. Page shapes come from real recorded fixtures
(`grants_gov_search2_page{1,2}.json`, captured live 2026-08-26); the fake
client replays them and synthesises variations without inventing response
fields.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.models import SourceFailure
from agent.tools.discovery import (
    GrantsGovSource,
    SourceError,
    discover_opportunities,
    keywords_for_profile,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _page(name: str) -> dict:
    """The `data` object from a recorded API response fixture."""
    return json.loads((FIXTURES / name).read_text())["data"]


def _hit(opp_id: str, open_date: str = "01/02/2026", close_date: str = "12/31/2099") -> dict:
    """A hit with the exact field names the real API returns."""
    template = dict(_page("grants_gov_search2_page1.json")["oppHits"][0])
    template.update({"id": opp_id, "openDate": open_date, "closeDate": close_date})
    return template


class FakeClient:
    """Replays canned pages keyed by (keyword, start_record)."""

    def __init__(self, pages: dict[tuple[str, int], tuple[list[dict], int]]):
        """`pages` maps `(keyword, start_record)` to a `(hits, hit_count)` pair, or the sentinel `"ERROR"`. A key that is absent returns an empty page, which is how "the end of the results" is expressed."""
        self.pages = pages
        self.search_calls: list[tuple[str, int]] = []
        self.detail_calls: list[str] = []
        self.detail_errors: set[str] = set()

    def search(self, keyword, rows=25, statuses="posted", start_record=0):
        """Replay one page, recording the call. `"ERROR"` raises `SourceError` instead.

        Recording `search_calls` is what lets a test assert the pagination
        arithmetic — which offsets were requested, and that the loop stopped
        when it should have.
        """
        self.search_calls.append((keyword, start_record))
        result = self.pages.get((keyword, start_record))
        if result is None:
            return [], 0
        if result == "ERROR":
            raise SourceError("search2 returned errorcode=1: fake failure")
        return result

    def fetch_opportunity(self, opportunity_id):
        """Replay a detail call, recording it. Ids in `detail_errors` raise instead.

        The recorded list is the evidence for the "filter before hydrating"
        property: a row dropped for being closed must never appear here.
        """
        self.detail_calls.append(opportunity_id)
        if opportunity_id in self.detail_errors:
            raise SourceError("fetchOpportunity failed: fake detail error")
        return {}


class TestPagination:
    """Walking pages: until the hit count, until the cap, or until an empty page."""

    def test_walks_pages_until_hit_count(self):
        pages = {
            ("k", 0): ([_hit("1"), _hit("2")], 5),
            ("k", 2): ([_hit("3"), _hit("4")], 5),
            ("k", 4): ([_hit("5")], 5),
        }
        source = GrantsGovSource(FakeClient(pages), keywords=("k",), rows_per_page=2, hydrate=False)
        found = source.fetch()
        assert sorted(o.id for o in found) == [f"grants_gov:{i}" for i in "12345"]

    def test_stops_at_max_per_keyword(self):
        pages = {
            ("k", 0): ([_hit("1"), _hit("2")], 100),
            ("k", 2): ([_hit("3"), _hit("4")], 100),
        }
        source = GrantsGovSource(
            FakeClient(pages), keywords=("k",), rows_per_page=2, max_per_keyword=3, hydrate=False
        )
        assert len(source.fetch()) == 3

    def test_caps_low_information_rows_after_search(self):
        hits = [_hit(str(index)) for index in range(8)]
        source = GrantsGovSource(
            FakeClient({("k", 0): (hits, len(hits))}),
            keywords=("k",),
            rows_per_page=10,
            hydrate=False,
            max_low_information=3,
        )

        assert len(source.fetch()) == 3

    def test_real_fixture_pages_are_distinct_and_both_consumed(self):
        p1, p2 = _page("grants_gov_search2_page1.json"), _page("grants_gov_search2_page2.json")
        # hitCount in the fixture is 228; cap so exactly two pages are read.
        pages = {
            ("k", 0): (p1["oppHits"], p1["hitCount"]),
            ("k", 3): (p2["oppHits"], p2["hitCount"]),
        }
        source = GrantsGovSource(
            FakeClient(pages),
            keywords=("k",),
            rows_per_page=3,
            max_per_keyword=6,
            hydrate=False,
            skip_past_deadlines=False,
        )
        found = source.fetch()
        assert len(found) == 6
        assert len({o.id for o in found}) == 6

    def test_empty_first_page_is_a_clean_empty_result(self):
        source = GrantsGovSource(FakeClient({}), keywords=("k",), hydrate=False)
        assert source.fetch() == []
        assert source.partial_failures == []


class TestDedup:
    """The same opportunity found by two keywords or on two pages collapses on id."""

    def test_duplicates_across_pages_and_keywords_collapse_on_id(self):
        pages = {
            ("a", 0): ([_hit("1"), _hit("2")], 2),
            ("b", 0): ([_hit("2"), _hit("3")], 2),
        }
        source = GrantsGovSource(FakeClient(pages), keywords=("a", "b"), hydrate=False)
        assert sorted(o.id for o in source.fetch()) == [
            "grants_gov:1",
            "grants_gov:2",
            "grants_gov:3",
        ]


class TestSinceAndDates:
    """Client-side date filtering. A missing open date is kept — absence is not evidence of age."""

    def test_since_filters_on_open_date_client_side(self):
        pages = {
            ("k", 0): ([_hit("old", open_date="01/01/2020"), _hit("new", open_date="08/01/2026")], 2)
        }
        source = GrantsGovSource(FakeClient(pages), keywords=("k",), hydrate=False)
        found = source.fetch(since=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert [o.id for o in found] == ["grants_gov:new"]

    def test_missing_open_date_is_kept_not_dropped(self):
        pages = {("k", 0): ([_hit("nodate", open_date="")], 1)}
        source = GrantsGovSource(FakeClient(pages), keywords=("k",), hydrate=False)
        found = source.fetch(since=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert len(found) == 1

    def test_past_deadlines_are_skipped_before_hydration(self):
        client = FakeClient(
            {("k", 0): ([_hit("dead", close_date="01/01/2020"), _hit("live")], 2)}
        )
        source = GrantsGovSource(client, keywords=("k",), hydrate=True)
        found = source.fetch()
        assert [o.id for o in found] == ["grants_gov:live"]
        assert client.detail_calls == ["live"]


class TestFailureReporting:
    """A dead page or detail call is reported and does not discard what already worked.

    The last case pins that `partial_failures` is reset per fetch, so a
    report never carries a previous run's failures.
    """

    def test_a_dead_page_keeps_earlier_pages_and_is_reported(self):
        pages = {
            ("k", 0): ([_hit("1"), _hit("2")], 6),
            ("k", 2): "ERROR",
        }
        source = GrantsGovSource(FakeClient(pages), keywords=("k",), rows_per_page=2, hydrate=False)
        found = source.fetch()
        assert len(found) == 2
        assert len(source.partial_failures) == 1
        assert "startRecordNum=2" in source.partial_failures[0].detail

    def test_a_dead_detail_call_is_reported_and_the_row_survives_unhydrated(self):
        client = FakeClient({("k", 0): ([_hit("1"), _hit("2")], 2)})
        client.detail_errors = {"1"}
        source = GrantsGovSource(client, keywords=("k",), hydrate=True)
        found = source.fetch()
        assert len(found) == 2
        assert any("fetchOpportunity id=1" in f.detail for f in source.partial_failures)

    def test_partial_failures_reach_discover_opportunities(self):
        pages = {("k", 0): "ERROR"}
        source = GrantsGovSource(FakeClient(pages), keywords=("k",), hydrate=False)
        found, failures = discover_opportunities([source], datetime(1970, 1, 1))
        assert found == []
        assert len(failures) == 1
        assert isinstance(failures[0], SourceFailure)

    def test_partial_failures_reset_between_fetches(self):
        pages = {("k", 0): "ERROR"}
        source = GrantsGovSource(FakeClient(pages), keywords=("k",), hydrate=False)
        source.fetch()
        source.fetch()
        assert len(source.partial_failures) == 1


class TestKeywordSelection:
    """Keywords derived from structured profile fields, degrading to the base set for anything unrecognised."""

    class _Profile:
        """The few profile fields keyword selection reads.

        A stub rather than a real profile, so a test cannot pass because of
        a field the selection logic does not actually consult.
        """

        degree_level = "undergrad"
        entity_type = "none"

    def test_profile_adds_degree_term_to_the_base_set(self):
        assert keywords_for_profile(self._Profile()) == (
            "student",
            "entrepreneurship",
            "undergraduate",
        )

    def test_entity_holders_also_search_small_business(self):
        profile = self._Profile()
        profile.entity_type = "llc"
        assert "small business innovation" in keywords_for_profile(profile)

    def test_unknown_degree_level_degrades_to_base_keywords(self):
        profile = self._Profile()
        profile.degree_level = "something_else"
        assert keywords_for_profile(profile) == ("student", "entrepreneurship")
