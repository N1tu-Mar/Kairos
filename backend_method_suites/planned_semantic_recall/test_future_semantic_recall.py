from __future__ import annotations

import pytest

from tests.factories import generated

pytestmark = pytest.mark.xfail(
    reason="Repository.recall currently matches normalized text, not semantic meaning.",
    strict=False,
)


def test_semantic_recall_reuses_equivalent_traction_question(memory_repo):
    memory_repo.remember_answer(
        "founder_demo",
        generated(
            "traction",
            "We have 40 active users and 12 customer interviews.",
            question="Describe your current traction.",
        ),
    )

    reused = memory_repo.recall(
        "founder_demo",
        "How many users, customers, or interviews do you have so far?",
    )

    assert reused is not None
    assert reused.status == "REUSED"
    assert "40 active users" in reused.answer
