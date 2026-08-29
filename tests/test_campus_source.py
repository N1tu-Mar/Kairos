"""Campus discovery as a runtime source. Offline, no network, no browser.

The behaviour under test is the review boundary: turning the flag on adds
*reviewed* campus rows and never fresh parser output, however good that
output looks.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent.tools.campus import CampusDiscoverySource, to_opportunity
from agent.tools.discovery import SourceError, discover_opportunities


def row(
    scrape_id="campus_fund:abc",
    review_status="NEEDS_HUMAN_REVIEW",
    **overrides,
):
    base = {
        "scrape_id": scrape_id,
        "title": "Campus Innovation Fund",
        "organization": "Example University",
        "source_url": "https://example.edu/fund",
        "award_min": 250,
        "award_max": 3000,
        "institution": ["Example University"],
        "degree_levels": ["undergraduate", "graduate"],
        "applicant_type": ["student"],
        "equity_required": None,
        "team_size_min": 1,
        "team_size_max": 5,
        "deadline": "Dec. 21st",
        "deadline_iso": None,
        "evidence": {
            "degree_levels": {
                "text": "Undergraduate and Graduate students enrolled at Example University",
                "source_url": "https://example.edu/fund",
                "method": "regex:eligibility_block",
            },
            "institution": {
                "text": "Undergraduate and Graduate students enrolled at Example University",
                "source_url": "https://example.edu/fund",
                "method": "regex:eligibility_block",
            },
            "team_size_min": {
                "text": "Teams can consist of 1 to 5 members",
                "source_url": "https://example.edu/fund",
                "method": "regex:team_range",
            },
            "team_size_max": {
                "text": "Teams can consist of 1 to 5 members",
                "source_url": "https://example.edu/fund",
                "method": "regex:team_range",
            },
        },
        "unknown_fields": ["equity_required"],
        "caveats": [],
        "founder_reviews": [],
        "fetch": {"url": "https://example.edu/fund", "raw_path": ""},
        "scraped_at": "2026-08-24T01:41:44.951838Z",
        "review_status": review_status,
    }
    base.update(overrides)
    return base


@pytest.fixture
def candidates(tmp_path):
    def write(rows):
        path = tmp_path / "campus.json"
        path.write_text(json.dumps(rows))
        return path

    return write


class TestTheFlag:
    """`KAIROS_ENABLE_BROWSER` is the only difference between yielding rows and yielding nothing.

    Disabled must not even read the file, so the default configuration cannot
    fail on a file it was never going to use.
    """

    def test_disabled_returns_nothing_and_does_not_read_the_file(self, tmp_path):
        source = CampusDiscoverySource(tmp_path / "does-not-exist.json", enabled=False)
        assert source.fetch() == []

    def test_enabled_loads_accepted_rows(self, candidates):
        source = CampusDiscoverySource(
            candidates([row(review_status="ACCEPTED")]), enabled=True
        )
        found = source.fetch()
        assert [o.id for o in found] == ["campus:campus_fund:abc"]
        assert found[0].source == "browser"

    def test_the_flag_is_the_only_difference(self, candidates):
        path = candidates([row(review_status="ACCEPTED")])
        assert CampusDiscoverySource(path, enabled=False).fetch() == []
        assert len(CampusDiscoverySource(path, enabled=True).fetch()) == 1


class TestTheReviewBoundary:
    """Only human-ACCEPTED rows become opportunities.

    Everything else is held back or reported.
    """

    @pytest.mark.parametrize("status", ["NEEDS_HUMAN_REVIEW", "REJECTED"])
    def test_unreviewed_and_rejected_rows_never_become_opportunities(
        self, candidates, status
    ):
        source = CampusDiscoverySource(
            candidates([row(review_status=status)]), enabled=True
        )
        assert source.fetch() == []

    def test_a_mixed_file_yields_only_the_accepted_rows(self, candidates):
        source = CampusDiscoverySource(
            candidates(
                [
                    row(scrape_id="a", review_status="ACCEPTED"),
                    row(scrape_id="b", review_status="NEEDS_HUMAN_REVIEW"),
                    row(scrape_id="c", review_status="REJECTED"),
                ]
            ),
            enabled=True,
        )
        assert [o.id for o in source.fetch()] == ["campus:a"]

    def test_an_accepted_row_with_no_page_is_skipped_and_reported(self, candidates):
        source = CampusDiscoverySource(
            candidates([row(review_status="ACCEPTED", source_url="")]), enabled=True
        )
        assert source.fetch() == []
        assert "no source_url" in source.partial_failures[0].detail


class TestDegradation:
    """How the source fails: a missing or malformed file raises, and a dead source does not end the run."""

    def test_a_missing_file_raises_rather_than_returning_empty(self, tmp_path):
        source = CampusDiscoverySource(tmp_path / "gone.json", enabled=True)
        with pytest.raises(SourceError):
            source.fetch()

    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        with pytest.raises(SourceError):
            CampusDiscoverySource(path, enabled=True).fetch()

    def test_a_dead_campus_source_does_not_kill_the_run(self, tmp_path):
        source = CampusDiscoverySource(tmp_path / "gone.json", enabled=True)
        found, failures = discover_opportunities([source], datetime(1970, 1, 1))
        assert found == []
        assert [f.source for f in failures] == ["browser"]


class TestLiveSweep:
    """The optional sweep: never implicit, and never able to add rows to the run that triggered it."""

    def test_no_sweep_happens_unless_explicitly_allowed(self, candidates):
        called = []

        source = CampusDiscoverySource(
            candidates([row(review_status="ACCEPTED")]),
            enabled=True,
            scrape_fn=lambda targets: called.append(targets) or ([], _run()),
        )
        source.fetch()
        assert called == []

    def test_a_sweep_cannot_add_rows_to_the_run_that_triggered_it(self, candidates):
        """The sweep writes NEEDS_HUMAN_REVIEW rows. This source ignores
        them, so a sweep can only ever affect a later run."""
        path = candidates([row(review_status="NEEDS_HUMAN_REVIEW")])
        source = CampusDiscoverySource(
            path,
            enabled=True,
            allow_live_scrape=True,
            scrape_fn=lambda targets: ([object()], _run()),
        )
        assert source.fetch() == []

    def test_sweep_failures_are_reported_not_swallowed(self, candidates):
        source = CampusDiscoverySource(
            candidates([]),
            enabled=True,
            allow_live_scrape=True,
            scrape_fn=lambda targets: ([], _run(failures=[("https://x.edu", "HTTP_500")])),
        )
        source.fetch()
        assert "HTTP_500" in source.partial_failures[0].detail

    def test_a_crashing_sweep_still_lets_reviewed_rows_through(self, candidates):
        def explode(targets):
            raise RuntimeError("playwright is not installed")

        source = CampusDiscoverySource(
            candidates([row(review_status="ACCEPTED")]),
            enabled=True,
            allow_live_scrape=True,
            scrape_fn=explode,
        )
        found = source.fetch()
        assert len(found) == 1
        assert "playwright is not installed" in source.partial_failures[0].detail


class _Failure:
    def __init__(self, url, failure):
        self.url = url
        self.failure = failure


def _run(failures=()):
    class Run:
        pass

    run = Run()
    run.failures = [_Failure(u, f) for u, f in failures]
    return run


class TestEligibilityMapping:
    """Scraped fields become structured eligibility only where evidence backs them.

    An unmappable term is dropped rather than guessed, and a yearless
    deadline stays a string rather than becoming a date.
    """

    def test_evidence_backed_fields_survive_and_are_translated(self):
        opportunity = to_opportunity(row(review_status="ACCEPTED"))
        assert opportunity.eligibility.degree_levels == ["undergrad", "masters", "phd"]
        assert opportunity.eligibility.min_team_size == 1
        assert opportunity.eligibility.max_team_size == 5

    def test_a_field_without_evidence_stays_unknown(self):
        r = row(review_status="ACCEPTED")
        r["equity_required"] = False  # value present, evidence absent
        opportunity = to_opportunity(r)
        assert opportunity.eligibility.takes_equity is None

    def test_an_unmappable_degree_term_is_dropped_not_guessed(self):
        r = row(review_status="ACCEPTED")
        r["degree_levels"] = ["alumni"]
        assert to_opportunity(r).eligibility.degree_levels is None

    def test_a_yearless_deadline_does_not_become_a_date(self):
        opportunity = to_opportunity(row(review_status="ACCEPTED"))
        assert opportunity.deadline is None

    def test_evidence_spans_are_carried_through_as_criteria(self):
        opportunity = to_opportunity(row(review_status="ACCEPTED"))
        assert any("Teams can consist of 1 to 5" in c.text for c in opportunity.criteria)

    def test_a_negated_span_cannot_grant_eligibility(self):
        r = row(review_status="ACCEPTED")
        r["degree_levels"] = ["undergraduate"]
        r["evidence"]["degree_levels"] = {
            "text": "Undergraduate students are not eligible for this fund",
            "source_url": "https://example.edu/fund",
            "method": "regex:eligibility_block",
        }
        assert to_opportunity(r).eligibility.degree_levels is None

    def test_the_archived_page_is_used_when_it_is_still_on_disk(self, tmp_path):
        archive = tmp_path / "page.html"
        archive.write_text("<p>Teams can consist of 1 to 5 members</p>")
        r = row(review_status="ACCEPTED")
        r["fetch"] = {"url": r["source_url"], "raw_path": str(archive)}
        # team size is supported by the archive; degree levels are not, and
        # the spans are no longer their own evidence.
        opportunity = to_opportunity(r)
        assert opportunity.eligibility.max_team_size == 5
        assert opportunity.eligibility.degree_levels is None

    def test_scraped_at_becomes_verified_at(self):
        opportunity = to_opportunity(row(review_status="ACCEPTED"))
        assert opportunity.verified is True
        assert opportunity.verified_at == datetime(
            2026, 8, 24, 1, 41, 44, 951838, tzinfo=timezone.utc
        )
