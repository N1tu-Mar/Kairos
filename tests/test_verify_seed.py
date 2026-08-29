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


FAQ = """
<html><body>
<h2>FAQ</h2>
<p>Teams may consist of 1 to 4 members.</p>
</body></html>
"""

URL = "https://example.edu/fund"
FAQ_URL = "https://example.edu/fund/faq"


def _row(criteria, source_url=URL):
    """`criteria` is a list of quote strings, or (quote, source_doc) pairs."""
    normalised = [c if isinstance(c, tuple) else (c, source_url) for c in criteria]
    return {
        "id": "x",
        "title": "Campus Innovation Fund",
        "source_url": source_url,
        "criteria": [{"text": t, "source_doc": doc} for t, doc in normalised],
    }


class TestNormalize:
    """Text normalisation: markup, entities, case and punctuation must not decide whether a quote is found."""

    def test_markup_entities_and_case_are_flattened(self):
        assert verify_seed.normalize("<b>Awards</b>&nbsp;Range") == "awards range"

    def test_punctuation_variants_match(self):
        assert verify_seed.normalize("teams of 1–5") == verify_seed.normalize("teams of 1-5")


class TestMissingEvidence:
    """A quote that is not on the page it cites is reported, including when it sits among good ones."""

    PAGES = {URL: PAGE, FAQ_URL: FAQ}

    def test_a_real_quote_is_found_across_markup_and_entities(self):
        row = _row(["undergraduate and graduate students enrolled full-time"])
        assert verify_seed.missing_evidence(self.PAGES, row) == []

    def test_a_fabricated_quote_is_reported(self):
        row = _row(["awards up to $50,000 for freshmen"])
        assert verify_seed.missing_evidence(self.PAGES, row) == [
            "awards up to $50,000 for freshmen"
        ]

    def test_one_bad_quote_among_good_ones_is_still_caught(self):
        row = _row(
            [
                "Awards range from $2,500 to $10,000 per team.",
                "no equity is taken",  # not on the page
            ]
        )
        assert verify_seed.missing_evidence(self.PAGES, row) == ["no equity is taken"]

    def test_a_quote_is_checked_against_the_sub_page_it_cites(self):
        """Programs state eligibility on FAQ and rules sub-pages. A quote
        that is real but lives on the sub-page must verify, not fail."""
        row = _row([("Teams may consist of 1 to 4 members.", FAQ_URL)])
        assert verify_seed.missing_evidence(self.PAGES, row) == []

    def test_a_quote_on_the_wrong_page_still_fails(self):
        """The same quote, cited to the landing page where it does not
        appear, is not evidence."""
        row = _row([("Teams may consist of 1 to 4 members.", URL)])
        assert verify_seed.missing_evidence(self.PAGES, row) != []

    def test_an_unfetchable_cited_page_is_reported_not_assumed_good(self):
        row = _row([("anything at all", "https://example.edu/fund/gone")])
        assert "could not be fetched" in verify_seed.missing_evidence(self.PAGES, row)[0]

    def test_a_row_with_no_criteria_checks_nothing(self):
        assert verify_seed.missing_evidence(self.PAGES, {"id": "x"}) == []


class TestEvidencePages:
    """Which pages a quote may be checked against — sub-pages of the same site, never another organisation's."""

    def test_sub_pages_of_the_same_site_are_fetched(self):
        wanted, refused = verify_seed.evidence_pages(
            _row([("q", FAQ_URL), ("q2", URL)])
        )
        assert set(wanted) == {FAQ_URL, URL}
        assert refused == []

    def test_a_quote_citing_another_organisation_is_refused(self):
        wanted, refused = verify_seed.evidence_pages(
            _row([("q", "https://someoneelse.org/rules")])
        )
        assert refused == ["https://someoneelse.org/rules"]
        assert wanted == []

    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("https://cep.mit.edu/x", "https://mit.edu/y", True),
            ("https://www.nasa.gov/x", "https://nasaorbit.org/rules", False),
            ("https://rbpc.rice.edu/eligibility", "https://rbpc.rice.edu", True),
        ],
    )
    def test_same_site_comparison(self, a, b, expected):
        assert verify_seed.same_site(a, b) is expected


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
