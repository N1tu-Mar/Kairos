"""Externally sourced values cannot become executable dashboard links."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.models import EligibilityQuestion
from agent.scraping.models import FetchRecord, ScrapedOpportunity
from agent.urls import validate_external_url
from tests.factories import opportunity


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "//evil.example/path",
        "https://user:password@example.com/grant",
        "https://example.com/bad\nheader",
        "https://example.com:invalid/grant",
        "https://example.com/unescaped space",
        "https://example.com\\@evil.example/grant",
        "not a URL",
    ],
)
def test_external_url_validator_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        validate_external_url(value)


@pytest.mark.parametrize(
    "value", ["https://example.com/grant", "http://legacy.example.org/program"]
)
def test_external_url_validator_accepts_normal_web_links(value):
    assert validate_external_url(value) == value


def test_opportunity_rejects_an_unsafe_source_url():
    with pytest.raises(ValidationError):
        opportunity(source_url="javascript:alert(1)")


def test_eligibility_question_rejects_an_unsafe_source_url():
    with pytest.raises(ValidationError):
        EligibilityQuestion(
            question_id="eq_1",
            founder_id="founder_1",
            opportunity_id="opp_1",
            opportunity_title="Grant",
            source_url="data:text/html,unsafe",
            check="ENTITY",
            question="Do you qualify?",
            requirement="Applicants must qualify.",
        )


def test_scraper_candidate_rejects_an_unsafe_source_url():
    with pytest.raises(ValidationError):
        ScrapedOpportunity(
            scrape_id="scrape_1",
            title="Grant",
            organization="Funder",
            source_url="https://user:secret@example.com/grant",
            fetch=FetchRecord(url="https://example.com/grant"),
        )
