"""The founder's own name and field of study, and why their email is not here.

`FounderProfile` is the eligibility surface: everything on it is either
compared against a funder's rules or read by the Drafter. `full_name` and
`major` are neither, which is why both are optional and why nothing in the
filter reads them. They exist so a founder recognises their own dashboard and
so a draft can address them by name.

There is no `email` field, and the first test here is the reason. Profiles are
redacted on the way into storage (`SqliteRepository.save_profile`), and the
redaction list treats an email address as PII that must never reach memory,
logs or telemetry. An address stored on this model would come back as
`[REDACTED_EMAIL]` — not an error, just silently wrong, which is worse. Contact
details belong to the identity provider.
"""

import pytest

from agent.models import FounderProfile
from agent.sanitize import redact


def _profile(**overrides) -> FounderProfile:
    """A minimal valid profile, so each test states only what it is about."""
    base = dict(
        founder_id="founder_test",
        degree_level="undergrad",
        institution="Rutgers University",
        citizenship="us_citizen",
        entity_type="none",
        team_size=2,
        stage="idea",
        funding_range=(1_000, 50_000),
        equity_ok=False,
        has_faculty_advisor=False,
        max_application_hours=8,
    )
    base.update(overrides)
    return FounderProfile(**base)


def test_an_email_would_not_survive_being_stored_on_a_profile():
    """The reason there is no `email` field. Not a wish, a demonstrated fact."""
    payload = _profile().model_dump_json()
    with_email = payload.replace(
        '"founder_id":"founder_test"',
        '"founder_id":"founder_test","email":"founder@rutgers.edu"',
    )
    assert "[REDACTED_EMAIL]" in redact(with_email)


def test_name_and_major_survive_redaction():
    """Neither is PII the redaction list recognises, so both round-trip intact."""
    profile = _profile(full_name="Ada Lovelace", major="Computer Science")
    restored = FounderProfile.model_validate_json(redact(profile.model_dump_json()))
    assert restored.full_name == "Ada Lovelace"
    assert restored.major == "Computer Science"


def test_both_fields_are_optional():
    """Every profile written before these fields existed must still load."""
    profile = _profile()
    assert profile.full_name is None
    assert profile.major is None


def test_a_profile_stored_before_these_fields_existed_still_validates():
    """The stored payload is JSON in a text column, so old rows are read back
    by this model directly. A required field here would break every one."""
    legacy = (
        '{"founder_id":"founder_demo","degree_level":"undergrad",'
        '"institution":"Rutgers","citizenship":"us_citizen","entity_type":"none",'
        '"team_size":1,"stage":"idea","traction":{},"funding_range":[1000,50000],'
        '"equity_ok":false,"has_faculty_advisor":false,"max_application_hours":8,'
        '"geographies":[],"knowledge_base":[]}'
    )
    profile = FounderProfile.model_validate_json(legacy)
    assert profile.founder_id == "founder_demo"
    assert profile.full_name is None


@pytest.mark.parametrize("field, limit", [("full_name", 200), ("major", 200)])
def test_both_fields_are_length_capped(field, limit):
    """`PUT /founders/{id}` replaces a profile wholesale, so every free-text
    field on it is a write an unauthenticated body could make unbounded."""
    with pytest.raises(ValueError):
        _profile(**{field: "x" * (limit + 1)})
