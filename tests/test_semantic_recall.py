"""Semantic answer recall: what may be reused, and what may never be.

Runs entirely offline — no AWS, no network, no model call. The default
matcher is deterministic by construction, so these assertions are stable
rather than approximately true on a good day.

The asymmetry that shapes every test here: a **false negative** costs the
founder one question they have already answered. A **false positive** puts
last application's answer onto this application's form, under the founder's
name, on a real funding submission. So the negative-pair and near-threshold
cases outnumber the happy path deliberately.
"""

from __future__ import annotations

import pytest

from agent.models import DraftField
from agent.semantic import (
    DEFAULT_THRESHOLD,
    BedrockEmbeddingMatcher,
    LexicalMatcher,
    is_reusable,
    tokenize,
)
from api.repository import SqliteRepository
from tests.factories import generated


@pytest.fixture
def repo():
    """An in-memory repository, optionally with a specific matcher and threshold."""
    return SqliteRepository("sqlite:///:memory:")


def remember(repo, founder: str, field: DraftField) -> None:
    """Store one answered field for the founder, so `recall` has something to find."""
    repo.remember_answer(founder, field)


# ── tier 1: exact matching still wins, unchanged ────────────────────────────


def test_exact_normalised_match_is_still_the_first_tier(repo):
    remember(
        repo,
        "founder_demo",
        generated("traction", "We have 40 active users.", question="Describe your traction to date!"),
    )

    reused = repo.recall("founder_demo", "Describe your traction to date")

    assert reused is not None
    assert reused.status == "REUSED"
    assert reused.reuse_match == "exact"
    assert reused.reuse_score == 1.0


def test_exact_match_is_preferred_over_a_semantic_one(repo):
    remember(
        repo,
        "founder_demo",
        generated("t1", "Exact answer.", question="Describe your traction to date."),
    )
    remember(
        repo,
        "founder_demo",
        generated("t2", "Semantic answer.", question="What traction do you have?"),
    )

    reused = repo.recall("founder_demo", "Describe your traction to date.")

    assert reused.answer == "Exact answer."
    assert reused.reuse_match == "exact"


# ── tier 2: semantic matching, the case the xfail used to pin ───────────────


def test_semantic_recall_reuses_an_equivalent_traction_question(repo):
    remember(
        repo,
        "founder_demo",
        generated(
            "traction",
            "We have 40 active users and 12 customer interviews.",
            question="Describe your current traction.",
        ),
    )

    reused = repo.recall(
        "founder_demo", "What traction do you have — users or interviews?"
    )

    assert reused is not None
    assert reused.status == "REUSED"
    assert "40 active users" in reused.answer


def test_a_semantic_reuse_can_explain_itself(repo):
    """A reuse the founder cannot interrogate is a reuse they cannot trust."""
    remember(
        repo,
        "founder_demo",
        generated(
            "team",
            "Two undergraduates.",
            question="Describe your team.",
        ),
    )

    reused = repo.recall("founder_demo", "Describe your founding team.")

    assert reused is not None
    assert reused.reuse_match == "lexical"
    assert reused.reuse_source_question == "Describe your team."
    assert reused.reuse_score >= DEFAULT_THRESHOLD
    assert reused.reused_from is not None


# ── negative pairs: questions that must NOT match ───────────────────────────


@pytest.mark.parametrize(
    "stored,asked",
    [
        ("Describe your traction to date.", "Describe your budget request."),
        ("Describe your team.", "Describe the problem you are solving."),
        ("What is your timeline?", "What is your legal entity structure?"),
        ("How many users do you have?", "How much funding are you requesting?"),
        ("Describe your traction.", "Who is your faculty advisor?"),
        ("What problem are you solving?", "What is your five-year revenue plan?"),
    ],
)
def test_unrelated_questions_are_not_reused(repo, stored, asked):
    remember(repo, "founder_demo", generated("f", "stored answer", question=stored))

    assert repo.recall("founder_demo", asked) is None


def test_a_compound_question_sharing_one_subject_is_not_reused(repo):
    """The tightest negative in the set, and the reason the threshold sits
    where it does. A question that asks about traction *and three other
    things* is not the traction question — reusing the old answer would
    leave three subjects silently unanswered."""
    remember(
        repo,
        "founder_demo",
        generated("t", "40 users.", question="Describe your traction."),
    )

    assert (
        repo.recall(
            "founder_demo",
            "Describe your traction, revenue, hiring plan and five-year roadmap in detail.",
        )
        is None
    )


def test_the_threshold_sits_above_every_negative_pair():
    """Pins the measured separation the default threshold is derived from.
    If a change narrows the gap, this fails before a bad reuse ships."""
    matcher = LexicalMatcher()
    negatives = [
        ("Describe your traction.", "Describe your traction, revenue, hiring plan and five-year roadmap in detail."),
        ("Describe your traction to date.", "Describe your budget request."),
        ("Describe your team.", "Describe the problem you are solving."),
        ("What is your timeline?", "What is your legal entity structure?"),
        ("How many users do you have?", "How much funding are you requesting?"),
        ("Describe your traction.", "Who is your faculty advisor?"),
        ("Please describe what you have done so far.", "Please describe what you will do next."),
    ]
    positives = [
        ("Describe your current traction.", "What traction do you have — users or interviews?"),
        (
            "Describe your current traction.",
            "How many users, customers, or interviews do you have so far?",
        ),
        ("Describe your team.", "Describe your founding team."),
        ("Describe your traction to date.", "What traction do you have?"),
        ("Describe your traction.", "Summarise adoption of the prototype."),
    ]

    worst_positive = min(matcher.similarity(a, b) for a, b in positives)
    best_negative = max(matcher.similarity(a, b) for a, b in negatives)

    assert best_negative < DEFAULT_THRESHOLD <= worst_positive


def test_shared_scaffolding_alone_is_not_a_match(repo):
    """Two questions can share every stopword and mean nothing alike."""
    remember(
        repo,
        "founder_demo",
        generated("f", "stored answer", question="Please describe what you have done so far."),
    )

    assert repo.recall("founder_demo", "Please describe what you will do next.") is None


# ── near-threshold behaviour ────────────────────────────────────────────────


def test_the_threshold_is_configurable(repo):
    """The same pair resolves differently either side of the threshold, which
    is what makes the threshold a real control rather than a constant."""
    remember(
        repo,
        "founder_demo",
        generated("f", "stored answer", question="Describe your traction."),
    )
    question = "Summarise adoption of the prototype."

    strict = SqliteRepository("sqlite:///:memory:", similarity_threshold=0.99)
    strict.engine = repo.engine  # same data, different policy
    assert strict.recall("founder_demo", question) is None

    loose = SqliteRepository("sqlite:///:memory:", similarity_threshold=0.05)
    loose.engine = repo.engine
    assert loose.recall("founder_demo", question) is not None


def test_a_score_just_under_the_threshold_is_refused(repo):
    matcher = LexicalMatcher()
    stored = "Describe your traction to date."
    asked = "What traction do you have?"
    score = matcher.similarity(stored, asked)

    remember(repo, "founder_demo", generated("f", "stored answer", question=stored))

    just_above = SqliteRepository("sqlite:///:memory:", similarity_threshold=score - 0.01)
    just_above.engine = repo.engine
    just_below = SqliteRepository("sqlite:///:memory:", similarity_threshold=score + 0.01)
    just_below.engine = repo.engine

    assert just_above.recall("founder_demo", asked) is not None
    assert just_below.recall("founder_demo", asked) is None


def test_disabling_the_matcher_leaves_exact_matching_only(repo):
    exact_only = SqliteRepository("sqlite:///:memory:", matcher=None)
    exact_only.engine = repo.engine
    remember(
        repo,
        "founder_demo",
        generated("f", "stored answer", question="Describe your current traction."),
    )

    assert exact_only.recall("founder_demo", "Describe your current traction.") is not None
    assert exact_only.recall("founder_demo", "What traction do you have?") is None


# ── cross-founder isolation ─────────────────────────────────────────────────


def test_an_answer_never_crosses_between_founders(repo):
    remember(
        repo,
        "founder_a",
        generated("traction", "Founder A has 40 users.", question="Describe your traction."),
    )

    assert repo.recall("founder_b", "Describe your traction.") is None
    assert repo.recall("founder_b", "What traction do you have?") is None


def test_identical_questions_from_two_founders_stay_separate(repo):
    remember(repo, "founder_a", generated("t", "A's answer", question="Describe your traction."))
    remember(repo, "founder_b", generated("t", "B's answer", question="Describe your traction."))

    assert repo.recall("founder_a", "Describe your traction.").answer == "A's answer"
    assert repo.recall("founder_b", "Describe your traction.").answer == "B's answer"


def test_semantic_candidates_are_scoped_to_one_founder(repo):
    """The candidate pool itself is scoped, not just the final result."""
    remember(
        repo,
        "founder_a",
        generated("t", "A's answer", question="Describe your current traction."),
    )

    assert repo.recall("founder_b", "What traction do you have so far?") is None


# ── protected fields are never reused ───────────────────────────────────────


PROTECTED_QUESTIONS = [
    "I certify that the information in this application is true.",
    "Applicant signature",
    "Electronic Signature of Authorized Representative",
    "Employer Identification Number (EIN)",
    "Social Security Number",
    "Bank routing number",
    "Debarment and suspension disclosure",
    "Conflict of interest statement",
    "SAM.gov Unique Entity ID (UEI)",
]


@pytest.mark.parametrize("question", PROTECTED_QUESTIONS)
def test_a_protected_field_is_never_reused_even_on_an_exact_match(repo, question):
    remember(repo, "founder_demo", generated("f", "Yes", question=question))

    assert repo.recall("founder_demo", question) is None


@pytest.mark.parametrize("question", PROTECTED_QUESTIONS)
def test_an_innocuous_answer_is_never_poured_into_a_protected_field(repo, question):
    """The incoming question is checked too. A stored answer about the team
    must not fill a signature box because the wording happened to be close."""
    remember(
        repo,
        "founder_demo",
        generated("f", "Two undergraduates.", question="Describe your team."),
    )

    assert repo.recall("founder_demo", question) is None


def test_an_unsupported_answer_is_not_reused(repo):
    field = generated("f", "A claim the auditor rejected.", question="Describe your traction.")
    field.audit_verdict = "UNSUPPORTED"
    remember(repo, "founder_demo", field)

    assert repo.recall("founder_demo", "Describe your traction.") is None


def test_an_unverifiable_answer_is_not_reused(repo):
    field = generated("f", "A claim the auditor could not check.", question="Describe your team.")
    field.audit_verdict = "UNVERIFIABLE"
    remember(repo, "founder_demo", field)

    assert repo.recall("founder_demo", "Describe your team.") is None


def test_a_needs_founder_field_has_nothing_to_reuse(repo):
    field = DraftField(
        field_id="f",
        question="Describe your traction.",
        answer=None,
        status="NEEDS_FOUNDER",
    )
    remember(repo, "founder_demo", field)

    assert repo.recall("founder_demo", "Describe your traction.") is None


def test_is_reusable_fails_closed_on_an_unknown_status():
    field = generated("f", "answer", question="Describe your traction.")
    field.status = "SOMETHING_NEW"  # type: ignore[assignment]

    ok, reason = is_reusable(field, "Describe your traction.")

    assert ok is False
    assert "not reusable" in reason


def test_is_reusable_names_the_protected_category():
    field = generated("f", "Yes", question="Applicant signature")

    ok, reason = is_reusable(field, "Applicant signature")

    assert ok is False
    assert "signature" in reason


# ── the matcher itself ──────────────────────────────────────────────────────


def test_the_lexical_matcher_is_deterministic():
    matcher = LexicalMatcher()
    scores = {
        matcher.similarity("Describe your traction.", "What traction do you have?")
        for _ in range(20)
    }
    assert len(scores) == 1


def test_similarity_is_symmetric():
    matcher = LexicalMatcher()
    a = matcher.similarity("Describe your team.", "Describe your founding team.")
    b = matcher.similarity("Describe your founding team.", "Describe your team.")
    assert a == b


def test_similarity_stays_in_range():
    matcher = LexicalMatcher()
    for left, right in [
        ("Describe your traction.", "Describe your traction."),
        ("", "Describe your traction."),
        ("Describe your traction.", "What is your bank routing number?"),
    ]:
        score = matcher.similarity(left, right)
        assert 0.0 <= score <= 1.0


def test_an_empty_question_matches_nothing():
    matcher = LexicalMatcher()
    assert matcher.similarity("", "Describe your traction.") == 0.0
    assert matcher.best_match("", ["Describe your traction."], threshold=0.1) is None


def test_tokenize_drops_scaffolding_and_folds_synonyms():
    assert tokenize("Please describe your current users.") == tokenize("What are the customers?")


def test_best_match_returns_the_highest_scorer():
    matcher = LexicalMatcher()
    match = matcher.best_match(
        "Describe your traction.",
        ["What is your budget?", "Describe your traction to date.", "Who is on the team?"],
        threshold=0.1,
    )
    assert match.question == "Describe your traction to date."
    assert match.backend == "lexical"


# ── the embedding backend: interface tested, live adapter unavailable ───────


def test_the_live_bedrock_backend_refuses_to_guess_a_model_id():
    """Nothing here has run against live Bedrock, so the adapter says so
    rather than reaching for a plausible-looking Titan model ID."""
    with pytest.raises(NotImplementedError) as exc:
        BedrockEmbeddingMatcher()

    assert "not wired up" in str(exc.value)


def test_the_embedding_interface_works_against_an_injected_backend():
    """The interface is exercised offline. This proves the shape is right;
    it does not prove any Bedrock model exists."""
    vectors = {
        "Describe your traction.": [1.0, 0.0, 0.0],
        "What traction do you have?": [0.9, 0.1, 0.0],
        "What is your bank routing number?": [0.0, 0.0, 1.0],
    }
    matcher = BedrockEmbeddingMatcher(embed=lambda text: vectors[text], model_id="[TEST]")

    near = matcher.similarity("Describe your traction.", "What traction do you have?")
    far = matcher.similarity("Describe your traction.", "What is your bank routing number?")

    assert near > 0.9
    assert far == 0.0


def test_an_injected_embedding_backend_drives_recall_end_to_end(repo):
    vectors = {
        "Describe your traction.": [1.0, 0.0],
        "How is adoption going?": [0.95, 0.05],
    }
    matcher = BedrockEmbeddingMatcher(embed=lambda text: vectors[text])
    embedded = SqliteRepository("sqlite:///:memory:", matcher=matcher, similarity_threshold=0.9)
    embedded.engine = repo.engine

    remember(
        repo,
        "founder_demo",
        generated("t", "40 users.", question="Describe your traction."),
    )

    reused = embedded.recall("founder_demo", "How is adoption going?")

    assert reused is not None
    assert reused.reuse_match == "bedrock-embedding"


def test_negative_cosine_is_reported_as_unrelated_not_opposite():
    matcher = BedrockEmbeddingMatcher(embed=lambda t: {"a": [1.0, 0.0], "b": [-1.0, 0.0]}[t])
    assert matcher.similarity("a", "b") == 0.0
