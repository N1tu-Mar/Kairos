"""The behaviour this suite was created to pin, now implemented.

This was `xfail` while `Repository.recall` matched normalised text only. It
stays exactly as written — the ground truth did not move, the implementation
caught up. `agent/semantic.py` adds a second tier behind the exact-match one,
and the full behaviour (negative pairs, near-threshold cases, cross-founder
isolation, protected fields) is exercised in `tests/test_semantic_recall.py`.
"""

from __future__ import annotations

from tests.factories import generated


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
