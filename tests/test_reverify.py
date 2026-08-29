"""Periodic reverification, offline against recorded pages.

The property that matters most is the last class in this file: the catalog is
never modified. Everything else is detection quality.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "reverify"

_SPEC = importlib.util.spec_from_file_location(
    "reverify", Path(__file__).parent.parent / "scripts" / "reverify.py"
)
reverify_mod = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("reverify", reverify_mod)
_SPEC.loader.exec_module(reverify_mod)

TODAY = date(2026, 8, 27)


@pytest.fixture
def fetcher():
    return reverify_mod.Fetcher(fixture_dir=FIXTURES)


def row(**overrides):
    base = {
        "id": "campus_fund",
        "title": "Campus Innovation Fund",
        "source_url": "https://example.edu/fund",
        "award_min": 2500,
        "award_max": 10000,
        "deadline": "2099-03-01",
        "verified": True,
        "verified_at": "2026-01-01T00:00:00+00:00",
        "criteria": [
            {
                "text": "Awards range from $2,500 to $10,000 per team.",
                "source_doc": "https://example.edu/fund#award",
            }
        ],
    }
    base.update(overrides)
    return base


class TestStaleness:
    """Which rows get re-fetched. Missing and unparseable timestamps are stale, never assumed fresh."""

    def test_a_row_verified_yesterday_is_fresh(self):
        assert not reverify_mod.is_stale(
            row(verified_at="2026-08-26T00:00:00+00:00"), 30, TODAY
        )

    def test_a_row_verified_last_year_is_stale(self):
        assert reverify_mod.is_stale(row(verified_at="2025-08-26T00:00:00+00:00"), 30, TODAY)

    def test_a_row_with_no_timestamp_is_stale_by_definition(self):
        assert reverify_mod.is_stale(row(verified_at=None), 30, TODAY)

    def test_an_unparseable_timestamp_is_stale_not_assumed_fresh(self):
        assert reverify_mod.is_stale(row(verified_at="last tuesday"), 30, TODAY)

    def test_fresh_rows_are_not_fetched(self, fetcher):
        report = reverify_mod.reverify(
            [row(verified_at="2026-08-26T00:00:00+00:00")],
            fetcher,
            max_age_days=30,
            today=TODAY,
            check_all=False,
        )
        assert report["rows_checked"] == 0
        assert report["rows_still_fresh"] == 1

    def test_all_overrides_the_freshness_window(self, fetcher):
        report = reverify_mod.reverify(
            [row(verified_at="2026-08-26T00:00:00+00:00")],
            fetcher,
            max_age_days=30,
            today=TODAY,
            check_all=True,
        )
        assert report["rows_checked"] == 1


class TestDetection:
    """Every way a verified row can go wrong: dead, redirected, rewritten, expired, or quietly retired behind a 200."""

    def test_an_unchanged_row_is_reported_unchanged(self, fetcher):
        assert reverify_mod.check(row(), fetcher, TODAY)["status"] == "UNCHANGED"

    def test_a_dead_page_is_detected(self, fetcher):
        finding = reverify_mod.check(
            row(source_url="https://example.edu/gone", criteria=[]), fetcher, TODAY
        )
        assert finding["status"] == "DEAD"
        assert finding["changes"][0]["now"] == "HTTP 404"

    def test_an_unreachable_host_is_dead_not_silently_fine(self, fetcher):
        finding = reverify_mod.check(
            row(source_url="https://example.edu/unreachable", criteria=[]), fetcher, TODAY
        )
        assert finding["status"] == "DEAD"

    def test_a_redirect_is_flagged(self, fetcher):
        finding = reverify_mod.check(
            row(source_url="https://example.edu/moved", criteria=[]), fetcher, TODAY
        )
        assert finding["status"] == "REDIRECTED"
        assert finding["changes"][0]["now"] == "https://example.edu/fund"

    def test_a_retired_page_that_still_returns_200_is_caught(self, fetcher):
        finding = reverify_mod.check(
            row(source_url="https://example.edu/retired", criteria=[]), fetcher, TODAY
        )
        assert finding["status"] == "TITLE_GONE"

    def test_a_rewritten_page_loses_its_evidence(self, fetcher):
        finding = reverify_mod.check(
            row(
                source_url="https://example.edu/rewritten",
                criteria=[
                    {
                        "text": "Awards range from $2,500 to $10,000 per team.",
                        "source_doc": "https://example.edu/rewritten#award",
                    }
                ],
            ),
            fetcher,
            TODAY,
        )
        assert finding["status"] == "EVIDENCE_LOST"
        assert "not found on the page it cites" in finding["changes"][0]["now"]

    def test_a_passed_deadline_is_detected(self, fetcher):
        finding = reverify_mod.check(row(deadline="2026-01-15"), fetcher, TODAY)
        assert finding["status"] == "DEADLINE_PASSED"
        assert "passed 224 days ago" in finding["changes"][0]["now"]

    def test_an_award_figure_missing_from_the_page_is_reported_as_a_hint(self, fetcher):
        finding = reverify_mod.check(row(award_max=99999), fetcher, TODAY)
        assert any(c["field"] == "award_max" for c in finding["changes"])

    def test_evidence_on_a_sub_page_is_checked_there(self, fetcher):
        finding = reverify_mod.check(
            row(
                criteria=[
                    {
                        "text": "Teams may consist of 1 to 4 members.",
                        "source_doc": "https://example.edu/fund/faq#team",
                    }
                ]
            ),
            fetcher,
            TODAY,
        )
        assert finding["status"] == "UNCHANGED"

    def test_a_row_with_no_url_is_dead(self, fetcher):
        finding = reverify_mod.check(row(source_url=""), fetcher, TODAY)
        assert finding["status"] == "DEAD"


class TestReport:
    """The report's shape: what needs review separated from what does not, and JSON-serialisable."""

    def test_the_report_separates_what_needs_review_from_what_does_not(self, fetcher):
        report = reverify_mod.reverify(
            [
                row(id="fine"),
                row(id="dead", source_url="https://example.edu/gone", criteria=[]),
            ],
            fetcher,
            max_age_days=30,
            today=TODAY,
            check_all=True,
        )
        assert report["unchanged"] == ["fine"]
        assert [f["id"] for f in report["needs_review"]] == ["dead"]
        assert report["by_status"] == {"UNCHANGED": 1, "DEAD": 1}

    def test_the_report_is_json_serialisable(self, fetcher):
        report = reverify_mod.reverify(
            [row()], fetcher, max_age_days=30, today=TODAY, check_all=True
        )
        json.dumps(report)


class TestNothingIsPromoted:
    """The load-bearing property: this script reports, it does not curate."""

    def test_the_input_rows_are_not_mutated(self, fetcher):
        rows = [row(id="dead", source_url="https://example.edu/gone", criteria=[])]
        before = json.dumps(rows, sort_keys=True)
        reverify_mod.reverify(
            rows, fetcher, max_age_days=30, today=TODAY, check_all=True
        )
        assert json.dumps(rows, sort_keys=True) == before

    def test_a_dead_row_keeps_its_verified_flag_in_the_catalog(self, fetcher):
        rows = [row(id="dead", source_url="https://example.edu/gone", criteria=[])]
        reverify_mod.reverify(
            rows, fetcher, max_age_days=30, today=TODAY, check_all=True
        )
        # Still True. Flipping it is a person's call, made after reading the
        # report — an automatic flip is a silent edit to curated data.
        assert rows[0]["verified"] is True

    def test_the_report_says_out_loud_that_nothing_was_changed(self, fetcher):
        report = reverify_mod.reverify(
            [row()], fetcher, max_age_days=30, today=TODAY, check_all=True
        )
        assert "was modified" in report["note"] or "nothing" in report["note"].lower()
