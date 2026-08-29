"""The discovery benchmark, scored against synthetic catalogs.

These tests are about the *scorer*, not about the catalog: a benchmark that
silently miscounts is worse than no benchmark, because the number gets
published. Each test hands the scorer a catalog it fully controls and asserts
the arithmetic.

The last class pins the separation that keeps the benchmark meaningful: its
ground truth is a version-controlled file, not something derived from the
data being scored.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

BENCH_PATH = Path(__file__).parent.parent / "scripts" / "run_discovery_benchmark.py"
REFERENCE_PATH = Path(__file__).parent / "discovery_benchmark" / "reference_set.json"

_SPEC = importlib.util.spec_from_file_location("discovery_benchmark", BENCH_PATH)
bench = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("discovery_benchmark", bench)
_SPEC.loader.exec_module(bench)

TODAY = date(2026, 8, 27)

REFERENCE = {
    "version": "test-1",
    "programs": [
        {
            "key": "alpha",
            "name": "Alpha Innovation Fund",
            "canonical_url": "https://alpha.edu/fund",
            "in_scope": True,
            "category": "campus_competition",
            "expected_award": {"min": 1000, "max": 5000},
            "expected_deadline": "2027-03-01",
            "expected_eligibility": {"degree_levels_include": ["undergrad"], "max_team_size": 4},
        },
        {
            "key": "beta",
            "name": "Beta National Prize",
            "canonical_url": "https://beta.org/prize",
            "in_scope": True,
            "category": "national_competition",
            "expected_award": {"min": None, "max": 20000},
            "expected_deadline": None,
            "expected_eligibility": {},
        },
        {
            "key": "gamma",
            "name": "Gamma Equity Accelerator",
            "canonical_url": "https://gamma.com/apply",
            "in_scope": False,
            "category": "equity_taking",
            "expected_award": {"min": None, "max": 100000},
            "expected_deadline": None,
            "expected_eligibility": {"takes_equity": True},
            "note": "takes equity",
        },
    ],
}


def row(**overrides):
    """One catalog row as JSON. Defaults match the reference set's first program, so a test varies only what it is about."""
    base = {
        "id": "alpha_fund",
        "title": "Alpha Innovation Fund",
        "source_url": "https://alpha.edu/fund",
        "deadline": "2027-03-01",
        "verified": True,
        "eligibility": {"degree_levels": ["undergrad"], "max_team_size": 4},
    }
    base.update(overrides)
    return base


def run(rows, campus_rows=(), forms=(), reference=None):
    """Score these rows against the reference set and return the result."""
    return bench.score(
        reference or REFERENCE, list(rows), list(campus_rows), set(forms), TODAY
    )


class TestRetrievalRecall:
    """What counts as retrieving a program: site and title matching, split by source channel.

    A campus row still awaiting review is scored as its own kind of miss —
    found but not usable is different from not found.
    """

    def test_an_empty_catalog_scores_zero(self):
        result = run([])
        assert result["retrieval_recall_pct"] == 0.0
        assert len(result["missed"]) == 2

    def test_a_matching_row_counts_as_retrieved(self):
        assert run([row()])["retrieval_recall_pct"] == 50.0

    def test_matching_ignores_our_own_ids(self):
        """The catalog's id is ours; scoring on it would grade our naming."""
        assert run([row(id="something-else-entirely")])["retrieved"] == 1

    def test_a_sub_domain_still_matches_the_program(self):
        assert run([row(source_url="https://apply.alpha.edu/fund")])["retrieved"] == 1

    def test_a_different_program_on_the_same_site_does_not_match(self):
        assert run([row(title="Alpha Dining Services Survey")])["retrieved"] == 0

    def test_source_level_recall_splits_by_channel(self):
        result = run([row()])
        assert result["source_recall"]["campus_competition"]["recall_pct"] == 100.0
        assert result["source_recall"]["national_competition"]["recall_pct"] == 0.0

    def test_a_campus_row_awaiting_review_is_a_distinct_kind_of_miss(self):
        result = run(
            [],
            campus_rows=[
                {"source_url": "https://alpha.edu/fund", "review_status": "NEEDS_HUMAN_REVIEW"}
            ],
        )
        alpha = next(m for m in result["missed"] if m["key"] == "alpha")
        assert "awaiting human review" in alpha["reason"]


class TestDuplicatesAndStaleness:
    """Two catalog rows for one program is a defect, and a passed deadline is counted stale."""

    def test_two_rows_for_one_program_are_reported_as_a_duplicate(self):
        result = run([row(id="a1"), row(id="a2")])
        assert result["duplicates"][0]["rows"] == ["a1", "a2"]

    def test_a_passed_deadline_is_counted_stale(self):
        result = run([row(deadline="2026-01-01")])
        assert len(result["stale_rows"]) == 1

    def test_a_future_deadline_is_not_stale(self):
        assert run([row()])["stale_rows"] == []


class TestDeadlineAccuracy:
    """An honest UNKNOWN deadline is counted separately from a wrong date. They are not the same error."""

    def test_an_exact_deadline_scores_exact(self):
        assert run([row()])["deadline_accuracy"]["exact"] == 1

    def test_an_honest_unknown_is_counted_apart_from_a_wrong_date(self):
        result = run([row(deadline=None)])
        assert result["deadline_accuracy"]["honestly_unknown"] == 1
        assert result["deadline_accuracy"]["wrong"] == 0

    def test_a_wrong_date_is_counted_wrong(self):
        result = run([row(deadline="2027-09-09")])
        assert result["deadline_accuracy"]["wrong"] == 1
        assert result["deadline_accuracy"]["exact"] == 0


class TestEligibilityScoring:
    """Coverage and precision move independently: an UNKNOWN lowers coverage, a wrong value lowers precision."""

    def test_carried_and_correct_facts_score_full_marks(self):
        result = run([row()])["structured_eligibility"]
        assert result["coverage_pct"] == 100.0
        assert result["precision_pct"] == 100.0

    def test_an_unknown_field_lowers_coverage_but_not_precision(self):
        result = run([row(eligibility={"degree_levels": ["undergrad"]})])["structured_eligibility"]
        assert result["coverage_pct"] == 50.0
        assert result["precision_pct"] == 100.0

    def test_a_wrong_value_lowers_precision(self):
        result = run(
            [row(eligibility={"degree_levels": ["phd"], "max_team_size": 4})]
        )["structured_eligibility"]
        assert result["coverage_pct"] == 100.0
        assert result["precision_pct"] == 50.0


class TestNegatives:
    """The deliberate negatives. Carrying one is fine; carrying one without its disqualifier is the defect."""

    def test_a_negative_carried_with_its_disqualifier_is_not_a_defect(self):
        result = run(
            [
                {
                    "id": "gamma",
                    "title": "Gamma Equity Accelerator",
                    "source_url": "https://gamma.com/apply",
                    "verified": True,
                    "eligibility": {"takes_equity": True},
                }
            ]
        )
        assert len(result["negatives_carried_and_correctly_marked"]) == 1
        assert result["negatives_carried_without_their_disqualifier"] == []

    def test_a_negative_carried_without_its_disqualifier_is_a_defect(self):
        result = run(
            [
                {
                    "id": "gamma",
                    "title": "Gamma Equity Accelerator",
                    "source_url": "https://gamma.com/apply",
                    "verified": True,
                    "eligibility": {},
                }
            ]
        )
        assert len(result["negatives_carried_without_their_disqualifier"]) == 1

    def test_negatives_do_not_count_toward_recall(self):
        result = run(
            [
                {
                    "id": "gamma",
                    "title": "Gamma Equity Accelerator",
                    "source_url": "https://gamma.com/apply",
                    "verified": True,
                    "eligibility": {"takes_equity": True},
                }
            ]
        )
        assert result["retrieval_recall_pct"] == 0.0


class TestVerificationAndForms:
    """An unverified hit still counts as retrieved but is flagged, and form coverage counts only retrieved programs."""

    def test_an_unverified_hit_is_retrieved_but_flagged(self):
        result = run([row(verified=False, verification_note="HTTP 403")])
        assert result["retrieved"] == 1
        assert result["retrieved_but_unverified"][0]["note"] == "HTTP 403"

    def test_form_coverage_counts_only_retrieved_programs(self):
        result = run([row()], forms={"alpha_fund"})
        assert result["form_coverage"]["programs_with_a_form"] == 1
        assert result["form_coverage"]["of_retrieved_pct"] == 100.0


class TestTheShippedReferenceSet:
    """The real file, checked for the properties that make it trustworthy."""

    @pytest.fixture
    def reference(self):
        """The shipped reference set, read from disk rather than constructed.

        These tests are about the real file — a fixture built in code
        would pass while the committed one had drifted.
        """
        return json.loads(REFERENCE_PATH.read_text())

    def test_it_is_version_controlled_and_versioned(self, reference):
        assert reference["version"]
        assert REFERENCE_PATH.exists()

    def test_it_contains_deliberate_negatives(self, reference):
        negatives = [p for p in reference["programs"] if not p["in_scope"]]
        assert len(negatives) >= 5, (
            "a reference set of only positives cannot detect a catalog that "
            "recommends equity deals and dead programs"
        )

    def test_every_program_carries_a_canonical_url_and_an_as_of_date(self, reference):
        for program in reference["programs"]:
            assert program["canonical_url"].startswith("https://")
            assert program["as_of"], f"{program['key']} has no as_of date"

    def test_keys_are_unique(self, reference):
        keys = [p["key"] for p in reference["programs"]]
        assert len(keys) == len(set(keys))

    def test_ground_truth_is_not_derived_from_the_catalog(self, reference):
        """The reference set must not simply mirror our own ids.

        If every entry keyed off a catalog id, the benchmark would be scoring
        the catalog against itself.
        """
        seed_path = Path(__file__).parent.parent / "data" / "opportunities.seed.json"
        catalog_ids = {r["id"] for r in json.loads(seed_path.read_text())}
        reference_keys = {p["key"] for p in reference["programs"]}
        assert not reference_keys <= catalog_ids

    def test_the_real_benchmark_runs_end_to_end(self, reference):
        seed = json.loads(
            (Path(__file__).parent.parent / "data" / "opportunities.seed.json").read_text()
        )
        result = bench.score(reference, seed, [], set(), TODAY)
        assert 0.0 <= result["retrieval_recall_pct"] <= 100.0
        json.dumps(result)
