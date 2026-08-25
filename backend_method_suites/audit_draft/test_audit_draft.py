from __future__ import annotations

import pytest

from agent.models import FieldAudit
from agent.subagents.auditor import ProposedAudit, audit_draft
from tests.factories import draft, generated, kb
from backend_method_suites.conftest import FakeAgent

pytestmark = pytest.mark.asyncio


async def test_supported_without_a_quote_is_downgraded_to_unverifiable():
    d = draft(generated("traction", "We have 40 active users."))
    agent = FakeAgent(
        ProposedAudit(fields=[FieldAudit(field_id="traction", verdict="SUPPORTED")])
    )

    report = await audit_draft(
        agent,
        "audit-v1",
        d,
        kb("The team has 40 active users.", traction={"users": 40}),
    )

    assert report.fields[0].verdict == "UNVERIFIABLE"
    assert d.fields[0].audit_verdict == "UNVERIFIABLE"
    assert "quoted no supporting span" in d.fields[0].audit_note


async def test_auditor_skipping_a_field_is_not_a_pass():
    d = draft(
        generated("venture", "LabQueue schedules shared lab equipment."),
        generated("traction", "We have 40 active users."),
    )
    agent = FakeAgent(
        ProposedAudit(
            fields=[
                FieldAudit(
                    field_id="venture",
                    verdict="SUPPORTED",
                    supporting_quote="LabQueue schedules shared lab equipment.",
                )
            ]
        )
    )

    report = await audit_draft(
        agent,
        "audit-v1",
        d,
        kb("LabQueue schedules shared lab equipment.", "The team has 40 active users."),
    )

    by_field = {field.field_id: field for field in report.fields}
    assert by_field["venture"].verdict == "SUPPORTED"
    assert by_field["traction"].verdict == "UNVERIFIABLE"
    assert "returned no verdict" in by_field["traction"].note
