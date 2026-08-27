"""The real application forms, and the fields the agent may never fill.

Two things are pinned here.

1.  **The forms load and stay honest.** Verbatim labels, a source URL, a
    retrieval date, and a visible `complete: false` on any form transcribed
    from a page that publishes only part of the application.
2.  **Every protected field survives a full drafting pass as NEEDS_FOUNDER.**
    Not "is on a list" — actually run the drafter with a knowledge base that
    contains a plausible answer, and assert the answer never lands in the
    field. A blocklist nobody exercises against the real labels is a blocklist
    that stops working the day a label is reworded.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agent.guardrails import blocklisted
from agent.models import ApplicationForm

FORMS_DIR = Path(__file__).parent.parent / "data" / "forms"
REAL_FORMS = [p for p in sorted(FORMS_DIR.glob("*.json")) if "demo" not in p.name]


def load(path: Path) -> ApplicationForm:
    return ApplicationForm.model_validate(json.loads(path.read_text()))


@pytest.fixture(params=REAL_FORMS, ids=lambda p: p.stem)
def form(request) -> ApplicationForm:
    return load(request.param)


class TestFormsAreWellFormed:
    def test_at_least_one_real_form_exists(self):
        assert REAL_FORMS, "data/forms/ holds only the synthetic demo form"

    def test_the_form_parses_against_the_runtime_contract(self, form):
        assert form.fields

    def test_every_form_records_where_and_when_it_was_read(self, form):
        assert form.source_url.startswith("https://")
        assert isinstance(form.retrieved_at, date)

    def test_no_field_label_is_empty(self, form):
        assert all(f.label.strip() for f in form.fields)

    def test_field_ids_are_unique(self, form):
        ids = [f.field_id for f in form.fields]
        assert len(ids) == len(set(ids))

    def test_a_partial_form_says_what_is_missing(self, form):
        if not form.complete:
            assert len(form.completeness_note) > 40, (
                "an incomplete form must say what is missing, or it reads as "
                "the whole application"
            )

    def test_no_real_form_carries_a_demo_marker(self, form):
        blob = json.dumps(form.model_dump(mode="json"))
        assert "[DEMO]" not in blob
        assert ".invalid" not in blob


class TestProtectedFields:
    """Certification, signature, disclosure, tax, payment and terms fields."""

    def test_curated_protected_fields_are_marked_or_caught(self, form):
        """Every field the curator marked protected must either trip the
        blocklist or carry a note explaining why a human still owns it."""
        for field in form.fields:
            if field.protected:
                assert blocklisted(field.label) or field.help_text, (
                    f"{field.field_id} is marked protected with no reason recorded"
                )

    def test_the_blocklist_catches_the_attestation_labels_verbatim(self):
        """The exact strings on the real forms, not paraphrases of them."""
        assert blocklisted("Affirmation of Policy Statement") == "attestation"
        assert blocklisted(
            "MIT CEP 2026 IP, Capital, and Revenue Disclosure Forms"
        ) == "disclosure"

    def test_a_reworded_label_still_trips_the_blocklist(self):
        assert blocklisted("I certify that the information is true")
        assert blocklisted("Applicant signature")
        assert blocklisted("Employer Identification Number (EIN)")


class TestProtectedFieldsAreNeverGenerated:
    """The end-to-end property, run through the real drafting path."""

    @pytest.mark.asyncio
    async def test_no_protected_field_is_ever_answered(self, form, monkeypatch):
        """A drafter that tries to answer everything, run against each real
        form. The protected fields must come back unanswered anyway."""
        from tests import factories
        from agent.subagents import drafter as drafter_mod

        profile = factories.profile()
        kb = factories.kb(
            "The team has a faculty advisor, Prof. Rivera, in Mechanical Engineering.",
            "The venture is incorporated in Delaware and has an EIN.",
            "We have 40 users and no revenue.",
            "The founder signs all institutional paperwork personally.",
        )
        opportunity = factories.opportunity(
            id=form.opportunity_id, title=form.name, source_url=form.source_url
        )

        async def answer_everything(agent, output_model, prompt, **kwargs):
            return output_model(
                fields=[
                    {
                        "field_id": spec.field_id,
                        "answer": "Yes — certified, signed, and disclosed in full.",
                        "provenance_quotes": [
                            "The founder signs all institutional paperwork personally."
                        ],
                    }
                    for spec in form.fields
                ]
            )

        monkeypatch.setattr(drafter_mod, "structured_call", answer_everything)

        draft = await drafter_mod.draft_application(
            agent=object(),
            prompt_version="test",
            draft_id="d1",
            budget=factories.budget(),
            form=form,
            opportunity=opportunity,
            profile=profile,
            kb=kb,
        )

        by_id = {f.field_id: f for f in draft.fields}
        for spec in form.fields:
            if spec.protected or blocklisted(spec.label):
                field = by_id[spec.field_id]
                assert field.status == "NEEDS_FOUNDER", (
                    f"{spec.field_id} ({spec.label!r}) was filled by the agent"
                )
                assert not field.answer, (
                    f"{spec.field_id} carries an answer despite being protected"
                )
