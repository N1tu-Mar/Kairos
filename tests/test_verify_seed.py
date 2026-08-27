"""The seed verifier's evidence check, offline.

`scripts/verify_seed.py` verifies two things: the page is reachable and
mentions the program, and every quoted evidence span in `criteria[].text`
actually appears on the page. These tests pin the second one — the guard
that stops a fabricated quote from surviving into the runtime seed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "verify_seed", Path(__file__).parent.parent / "scripts" / "verify_seed.py"
)
verify_seed = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("verify_seed", verify_seed)
_SPEC.loader.exec_module(verify_seed)


PAGE = """
<html><body>
<h1>Campus Innovation Fund</h1>
<p>Open to undergraduate&nbsp;and graduate students enrolled full-time.</p>
<p>Awards range from $2,500 to $10,000 per team.</p>
<p>Applications close March&nbsp;1, 2027.</p>
</body></html>
"""


def _row(criteria_texts):
    return {
        "id": "x",
        "title": "Campus Innovation Fund",
        "source_url": "https://example.edu/fund",
        "criteria": [{"text": t, "source_doc": "https://example.edu/fund"} for t in criteria_texts],
    }


class TestNormalize:
    def test_markup_entities_and_case_are_flattened(self):
        assert verify_seed.normalize("<b>Awards</b>&nbsp;Range") == "awards range"

    def test_punctuation_variants_match(self):
        assert verify_seed.normalize("teams of 1–5") == verify_seed.normalize("teams of 1-5")


class TestMissingEvidence:
    def test_a_real_quote_is_found_across_markup_and_entities(self):
        row = _row(["undergraduate and graduate students enrolled full-time"])
        assert verify_seed.missing_evidence(PAGE, row) == []

    def test_a_fabricated_quote_is_reported(self):
        row = _row(["awards up to $50,000 for freshmen"])
        assert verify_seed.missing_evidence(PAGE, row) == [
            "awards up to $50,000 for freshmen"
        ]

    def test_one_bad_quote_among_good_ones_is_still_caught(self):
        row = _row(
            [
                "Awards range from $2,500 to $10,000 per team.",
                "no equity is taken",  # not on the page
            ]
        )
        assert verify_seed.missing_evidence(PAGE, row) == ["no equity is taken"]

    def test_a_row_with_no_criteria_checks_nothing(self):
        assert verify_seed.missing_evidence(PAGE, {"id": "x"}) == []


class TestVerifyEndToEnd:
    """`verify()` with the network stubbed via httpx monkeypatching."""

    @pytest.fixture
    def fetch_page(self, monkeypatch):
        def fake_get(url, **kwargs):
            request = httpx.Request("GET", url)
            return httpx.Response(200, text=PAGE, request=request)

        monkeypatch.setattr(verify_seed.httpx, "get", fake_get)

    def test_good_evidence_verifies(self, fetch_page):
        row = _row(["Applications close March 1, 2027."])
        out = verify_seed.verify(row, timeout_s=1.0)
        assert out["verified"] is True
        assert "1 evidence quote(s) found" in out["verification_note"]

    def test_fabricated_evidence_fails_despite_http_200(self, fetch_page):
        row = _row(["open to high school students"])
        out = verify_seed.verify(row, timeout_s=1.0)
        assert out["verified"] is False
        assert out["verified_at"] is None
        assert "not found" in out["verification_note"]

    def test_missing_url_fails(self):
        out = verify_seed.verify({"id": "x", "title": "T"}, timeout_s=1.0)
        assert out["verified"] is False
