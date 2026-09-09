"""Model-role routing is explicit, centralized, and auditable offline."""

import importlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent import config
from agent.model_routing import ROLE_ROUTES, call_receipt, route_for
from agent.models import ModelCallReceipt


def test_every_planned_role_has_one_explicit_tier():
    assert {role: spec.tier for role, spec in ROLE_ROUTES.items()} == {
        "intake_interviewer": "reasoning",
        "eligibility_equivalence": "classify",
        "application_field_mapper": "classify",
        "opportunity_assessor": "classify",
        "application_drafter": "classify",
        "draft_auditor": "reasoning",
        "interactive_scout": "classify",
        "scheduled_orchestration": "deterministic",
    }


def test_routes_resolve_deployment_models_and_temperature_policy(monkeypatch):
    monkeypatch.setenv("KAIROS_DRAFTING_TEMPERATURE", "0.35")
    config.settings.cache_clear()

    intake = route_for("intake_interviewer")
    assessor = route_for("opportunity_assessor")
    drafter = route_for("application_drafter")
    auditor = route_for("draft_auditor")
    scheduled = route_for("scheduled_orchestration")

    assert (intake.model_id, intake.tier, intake.temperature) == (
        "[DEMO]reasoning-model",
        "reasoning",
        0.0,
    )
    assert (assessor.model_id, assessor.tier, assessor.temperature) == (
        "[DEMO]classify-model",
        "classify",
        0.0,
    )
    assert (drafter.model_id, drafter.tier, drafter.temperature) == (
        "[DEMO]classify-model",
        "classify",
        0.35,
    )
    assert (auditor.model_id, auditor.tier, auditor.temperature) == (
        "[DEMO]reasoning-model",
        "reasoning",
        0.0,
    )
    assert (scheduled.model_id, scheduled.tier, scheduled.max_tokens) == (
        "",
        "deterministic",
        0,
    )


@pytest.mark.parametrize(
    ("module_name", "expected_role"),
    [
        ("assessor", "opportunity_assessor"),
        ("drafter", "application_drafter"),
        ("auditor", "draft_auditor"),
        ("eligibility_reuse", "eligibility_equivalence"),
        ("field_mapper", "application_field_mapper"),
        ("intake_interviewer", "intake_interviewer"),
    ],
)
def test_each_subagent_factory_declares_its_role(monkeypatch, module_name, expected_role):
    module = importlib.import_module(f"agent.subagents.{module_name}")
    captured = {}

    def fake_build_subagent(**kwargs):
        captured.update(kwargs)
        return object(), object()

    monkeypatch.setattr(module, "build_subagent", fake_build_subagent)
    module.build()

    assert captured["role"] == expected_role
    assert "tier" not in captured
    assert "temperature" not in captured


def test_server_receipt_records_only_the_current_call_usage():
    before = (100, 40, 140, 0.25)
    budget = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=180,
            output_tokens=70,
            total_tokens=250,
            usd_estimate=0.41,
        )
    )

    receipt = call_receipt("opportunity_assessor", "prompt-v2", before, budget)

    assert receipt.role == "opportunity_assessor"
    assert receipt.tier == "classify"
    assert receipt.model_id == "[DEMO]classify-model"
    assert receipt.prompt_version == "prompt-v2"
    assert (receipt.input_tokens, receipt.output_tokens, receipt.total_tokens) == (
        80,
        30,
        110,
    )
    assert receipt.usd_estimate == pytest.approx(0.16)


def test_deterministic_work_cannot_be_mislabeled_as_a_model_call():
    with pytest.raises(ValidationError, match="deterministic role"):
        ModelCallReceipt(
            role="scheduled_orchestration",
            tier="deterministic",
            model_id="not-a-model",
            prompt_version="none",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            usd_estimate=0,
        )


def test_a_role_cannot_be_stamped_with_another_roles_tier():
    with pytest.raises(ValidationError, match="registered role"):
        ModelCallReceipt(
            role="draft_auditor",
            tier="classify",
            model_id="[DEMO]classify-model",
            prompt_version="auditor-v1",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            usd_estimate=0,
        )
