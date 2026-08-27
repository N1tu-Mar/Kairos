"""Spelled-out quantities and the numeric whitelist.

The digit extractor caught "400" but not "four hundred", so an unsupported
quantity written as words walked straight past check 3. These tests pin the
word-number extension: digit and word forms normalise to the same value, the
whitelist stays symmetric (a spelled number in the deck permits the digit
form and vice versa), and ordinary indefinite articles are not numbers.
"""

from __future__ import annotations

import pytest

from agent.guardrails import extract_numbers, ship_gate
from tests.factories import draft, generated, kb, opportunity


# ── extraction: word forms normalise to comparable values ───────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("forty users", {40.0}),
        ("twelve interviews", {12.0}),
        ("one hundred applicants", {100.0}),
        ("two thousand dollars", {2000.0}),
        ("forty-five percent", {45.0}),
        ("twenty five customers", {25.0}),
        ("twenty-five customers", {25.0}),
        ("seventeen pilots", {17.0}),
        ("ninety-nine problems", {99.0}),
        ("three hundred and twelve signups", {312.0}),
        ("a hundred users", {100.0}),
        ("a thousand downloads", {1000.0}),
        ("two million people", {2_000_000.0}),
    ],
)
def test_spelled_quantities_are_extracted(text, expected):
    assert extract_numbers(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1.5 million users", {1_500_000.0}),
        ("2 thousand dollars", {2000.0}),
        ("$3 million in savings", {3_000_000.0}),
    ],
)
def test_digit_plus_scale_word_is_one_value_not_two(text, expected):
    """'1.5 million' must not decompose into an asserted 1.5 and a spurious
    million — the pair is a single quantity."""
    assert extract_numbers(text) == expected


def test_digit_forms_still_extract_exactly_as_before():
    assert extract_numbers("$5,000 and 40% of 1.2M plus 5K") == {
        5000.0,
        40.0,
        1_200_000.0,
    }


def test_mixed_digits_and_words_extract_both():
    assert extract_numbers("40 users and twelve interviews") == {40.0, 12.0}


# ── false positives that must NOT become numeric claims ─────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "a founder from campus",
        "an application to a fund",
        "one of the first teams to try this",
        "no one on the team has raised before",
        "someone should review this",
    ],
)
def test_articles_and_idiomatic_one_are_not_numeric_claims(text):
    assert extract_numbers(text) == set()


def test_hundreds_plural_alone_is_not_a_precise_claim():
    # "hundreds of students" asserts no specific number to whitelist-check.
    assert extract_numbers("hundreds of students") == set()


# ── the whitelist end to end ────────────────────────────────────────────────


def test_unsupported_spelled_quantity_is_blocked():
    knowledge = kb("The team has 40 active users.", traction={"users": 40})
    d = draft(generated("traction", "We now serve four hundred users."))

    result = ship_gate(d, knowledge, opportunity=opportunity())

    assert result.passed is False
    assert result.failed_check == "NUMERIC_WHITELIST"
    assert "400" in result.violations[0].detail


def test_supported_spelled_quantity_ships():
    """'forty users' in the draft, '40 users' in the deck — same value."""
    knowledge = kb("The team has 40 active users.", traction={"users": 40})
    d = draft(generated("traction", "We have forty active users."))

    assert ship_gate(d, knowledge, opportunity=opportunity()).passed is True


def test_spelled_number_in_the_deck_permits_the_digit_form():
    knowledge = kb("Forty students used the pilot.", traction={"users": 40})
    d = draft(generated("traction", "We have 40 users."))

    assert ship_gate(d, knowledge, opportunity=opportunity()).passed is True


def test_unsupported_spelled_currency_is_blocked():
    knowledge = kb("The team has 40 active users.", traction={"users": 40})
    d = draft(generated("budget", "We already secured two thousand dollars."))

    result = ship_gate(d, knowledge, opportunity=opportunity())

    assert result.passed is False
    assert result.failed_check == "NUMERIC_WHITELIST"


def test_unsupported_spelled_percentage_is_blocked():
    knowledge = kb("The team has 40 active users.", traction={"users": 40})
    d = draft(generated("growth", "Retention improved forty-five percent."))

    result = ship_gate(d, knowledge, opportunity=opportunity())

    assert result.passed is False
    assert result.failed_check == "NUMERIC_WHITELIST"


def test_unsupported_decimal_scale_quantity_is_blocked():
    knowledge = kb("The team has 40 active users.", traction={"users": 40})
    d = draft(generated("market", "The market spans 1.5 million students."))

    result = ship_gate(d, knowledge, opportunity=opportunity())

    assert result.passed is False
    assert result.failed_check == "NUMERIC_WHITELIST"


def test_supported_decimal_scale_quantity_ships():
    knowledge = kb(
        "The addressable market is 1.5 million students.", traction={"users": 40}
    )
    d = draft(generated("market", "The market spans 1.5 million students."))

    assert ship_gate(d, knowledge, opportunity=opportunity()).passed is True


def test_hyphenated_compound_is_blocked_when_unsupported():
    knowledge = kb("The team has 40 active users.", traction={"users": 40})
    d = draft(generated("traction", "We completed twenty-five interviews."))

    result = ship_gate(d, knowledge, opportunity=opportunity())

    assert result.passed is False
    assert result.failed_check == "NUMERIC_WHITELIST"
