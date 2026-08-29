"""Semantic question matching for answer recall.

`Repository.recall` answers one question: *has the founder already answered
something that means the same thing?* Exact-after-normalisation matching gets
the easy half and undercounts the rest, which is the safe direction but leaves
"application 1 needed 15 answers, this one needs 3" more conservative than it
has to be.

This module adds the second tier behind a narrow interface. Three properties
shape every decision in here:

1.  **Exact match always wins.** The semantic tier only ever runs when
    normalised equality found nothing. A high-confidence path is never
    replaced by a probabilistic one.
2.  **A false negative costs one founder question. A false positive puts last
    application's answer on this application's form.** Those are not
    comparable, so the threshold is deliberately high and every ambiguous
    case resolves to "no match".
3.  **Reuse is not permitted for every field.** Certifications, signatures,
    tax identifiers, payment details and disclosures are re-asked every time,
    by the same blocklist that governs drafting — see
    `agent.guardrails.FIELD_BLOCKLIST`. So are answers that were blocked,
    unaudited or unsupported when they were written. `is_reusable` is the
    gate, and it fails closed.

The backend is pluggable. `LexicalMatcher` is the default: deterministic,
offline, no model call, no network — which is what lets the whole test suite
run with no AWS account. `BedrockEmbeddingMatcher` is the interface for a real
embedding backend; it is **not validated against live Bedrock** and raises
rather than guessing at a model ID (see DECISIONS.md D10).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent.guardrails import blocklisted
from agent.models import DraftField

#: Similarity a candidate must clear to be reused. Set from the measured
#: separation on the pairs in `tests/test_semantic_recall.py`: the highest
#: scoring true negative there is a compound question that shares its subject
#: but adds three more (0.562), and the lowest scoring real rephrasing is
#: 0.667. 0.65 sits in that gap, nearer the negatives than the positives —
#: a tie goes to asking the founder again.
DEFAULT_THRESHOLD = 0.65

#: Words that carry no topic signal. Removed before comparison so
#: "Describe your current traction" and "What is your traction so far?" are
#: not separated by their scaffolding.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on",
        "at", "for", "with", "about", "as", "by", "from", "is", "are", "was",
        "were", "be", "been", "being", "do", "does", "did", "have", "has",
        "had", "you", "your", "yours", "we", "our", "ours", "us", "i", "my",
        "it", "its", "this", "that", "these", "those", "there", "here",
        "what", "which", "who", "whom", "whose", "when", "where", "why",
        "how", "please", "describe", "explain", "tell", "provide", "list",
        "give", "briefly", "current", "currently", "so", "far", "date",
        "any", "all", "some", "much", "many", "more", "most", "up", "out",
        "into", "over", "than", "then", "them", "they",
    }
)

#: Words that mean the same thing on a funding form. Each set collapses to
#: its first member before comparison. Hand-written and small on purpose: a
#: broad synonym table is how unrelated questions start matching.
#:
#: The traction set is the one place a *concept* is folded rather than a
#: strict synonym. "Describe your traction" and "how many users or interviews
#: do you have" are the same question on a student funding form, and they
#: share no vocabulary at all — which is exactly the gap that made
#: normalised-text recall undercount. It stays one set rather than a general
#: relatedness model: everything in it is a way of asking how many people
#: have used the thing.
_SYNONYM_SETS: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "traction", "usage", "adoption", "uptake",
            "user", "users", "customer", "customers",
            "student", "students", "signup", "signups",
            "interview", "interviews",
        }
    ),
    frozenset({"team", "founders", "founder", "cofounders", "members"}),
    frozenset({"problem", "issue", "need", "pain"}),
    frozenset({"solution", "product", "prototype", "tool"}),
    frozenset({"budget", "funding", "money", "amount", "award"}),
    frozenset({"timeline", "schedule", "plan", "milestones"}),
    frozenset({"impact", "outcome", "outcomes", "results"}),
    frozenset({"venture", "startup", "company", "project"}),
)

_CANONICAL: dict[str, str] = {}
for _group in _SYNONYM_SETS:
    _head = sorted(_group)[0]
    for _word in _group:
        _CANONICAL[_word] = _head


def tokenize(question: str) -> list[str]:
    """Content words of a question, canonicalised. Deterministic."""
    words = re.findall(r"[a-z0-9]+", (question or "").lower())
    out: list[str] = []
    for word in words:
        if word in _STOPWORDS:
            continue
        # A crude plural fold, applied before the synonym table so
        # "users"/"user" reach the same canonical head either way.
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            singular = word[:-1]
            word = singular if singular not in _CANONICAL or word not in _CANONICAL else word
        out.append(_CANONICAL.get(word, word))
    return out


# ── Reuse eligibility ────────────────────────────────────────────────────────

#: Statuses whose answers may be carried forward. A NEEDS_FOUNDER field has
#: no answer to reuse, and anything else is a status this code does not know
#: about, which fails closed.
_REUSABLE_STATUSES = frozenset({"KNOWN", "GENERATED", "REUSED"})


def is_reusable(field: DraftField, question: str) -> tuple[bool, str]:
    """May this stored answer be offered for this question? `(ok, reason)`.

    Fails closed. Every rejection carries a machine-readable reason so a
    founder asking "why am I answering this again?" gets an answer, and so a
    reviewer can see the protected classes are actually enforced rather than
    documented.
    """
    if not (field.answer or "").strip():
        return False, "stored answer is empty"
    if field.status not in _REUSABLE_STATUSES:
        return False, f"status {field.status} is not reusable"

    # The protected field families, checked against BOTH the stored question
    # and the incoming one. A certification answered once is still a
    # certification, and an innocuous stored answer must not be poured into a
    # certification field on this form.
    for label in (field.question, question, field.field_id):
        category = blocklisted(label or "")
        if category:
            return False, f"protected field type: {category}"

    if field.audit_verdict in {"UNSUPPORTED", "UNVERIFIABLE"}:
        return False, f"audit verdict was {field.audit_verdict}"
    return True, "reusable"


# ── The matcher interface ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Match:
    """One candidate and why it was or was not selected.

    `score` and `backend` are stored on the reused field so the dashboard can
    explain a reuse rather than asserting one.
    """

    question: str
    score: float
    backend: str


@runtime_checkable
class SemanticMatcher(Protocol):
    """Anything that can rank stored questions against a new one."""

    name: str

    def similarity(self, left: str, right: str) -> float:
        """Similarity in [0, 1]. 1.0 means identical meaning."""
        ...

    def best_match(
        self, question: str, candidates: list[str], *, threshold: float
    ) -> Match | None:
        """Highest-scoring candidate at or above `threshold`, else None."""
        ...


class _BaseMatcher:
    """Shared `best_match` for matchers that only define `similarity`.

    Subclasses set `name`, which is recorded on the reused `DraftField` as
    `reuse_match` — so a reuse can name which backend produced it rather than
    asserting a bare score.
    """

    name = "base"

    def similarity(self, left: str, right: str) -> float:  # pragma: no cover
        """Score two questions in [0, 1]. Subclass responsibility."""
        raise NotImplementedError

    def best_match(
        self, question: str, candidates: list[str], *, threshold: float
    ) -> Match | None:
        """Highest-scoring candidate at or above `threshold`, else None.

        Linear scan, strictly-greater comparison — so on a tie the earliest
        candidate wins, which makes the result stable for a given candidate
        order. Callers pass candidates from a database query whose order is not
        guaranteed, so identical scores can resolve differently between runs.
        """
        best: Match | None = None
        for candidate in candidates:
            score = self.similarity(question, candidate)
            if score >= threshold and (best is None or score > best.score):
                best = Match(question=candidate, score=score, backend=self.name)
        return best


class LexicalMatcher(_BaseMatcher):
    """The default backend: offline, deterministic, no model call.

    Cosine similarity over a bag of canonicalised content words, with a
    length-mismatch penalty. It is not a language model and does not pretend
    to be one — it recognises rephrasings that share vocabulary after
    stopword removal and synonym folding, and it declines everything else.

    That is deliberately the conservative half of the problem. Everything it
    misses costs the founder one question they have already answered;
    everything a looser matcher would catch it would also have to risk
    getting wrong on a real application.
    """

    name = "lexical"

    def similarity(self, left: str, right: str) -> float:
        """Coverage of the shorter question's content words, diluted by the extra topics in the longer one.

        Symmetric, and zero whenever the two share no content word at all. The
        two-part shape is explained inline below; the short version is that plain
        cosine punishes exactly the short-vs-slightly-longer pairs that are the
        real rephrasings.
        """
        a, b = set(tokenize(left)), set(tokenize(right))
        if not a or not b:
            return 0.0

        shared = a & b
        if not shared:
            # No content word in common. Nothing here can distinguish "your
            # timeline" from "your entity structure", so the honest answer is
            # zero rather than a small number that looks like weak evidence.
            return 0.0

        # How much of the *shorter* question the overlap accounts for. This
        # is the signal: after stopwords and synonym folding, a question
        # often reduces to one or two content words, and plain cosine
        # punishes exactly the short-vs-slightly-longer pairs that are the
        # real rephrasings ("Describe your team" / "Describe your founding
        # team").
        coverage = len(shared) / min(len(a), len(b))

        # Then charge for the topics the longer question adds. One extra
        # qualifier is cheap; a whole second subject is not. This is what
        # keeps "Describe your traction." from matching a compound question
        # about traction, revenue, hiring and a five-year roadmap.
        extra = max(len(a), len(b)) - len(shared)
        dilution = 1.0 / (1.0 + extra)
        return coverage * (0.5 + 0.5 * dilution)


class BedrockEmbeddingMatcher(_BaseMatcher):
    """Embedding-backed matching. **Not validated against live Bedrock.**

    The interface exists and is exercised offline by injecting `embed`. The
    live adapter is deliberately unavailable: no model ID is hardcoded, no
    price is assumed, and nothing here has ever run against a real endpoint
    (DECISIONS.md, environment notes). Constructing it without an explicit
    `embed` callable raises rather than reaching for a plausible-looking
    Titan model ID.
    """

    name = "bedrock-embedding"

    def __init__(self, embed=None, *, model_id: str = "") -> None:
        """Requires an explicit `embed` callable. Constructing without one raises rather than reaching for a plausible-looking model ID — the same no-guessing rule as `agent/config.py`."""
        if embed is None:
            raise NotImplementedError(
                "The live Bedrock embedding backend is not wired up. No model ID "
                "has been confirmed against a real account, and guessing one is "
                "the failure mode agent/config.py exists to prevent. Pass an "
                "`embed` callable, or use LexicalMatcher."
            )
        self._embed = embed
        self.model_id = model_id

    def similarity(self, left: str, right: str) -> float:
        """Cosine similarity of the two embeddings, clamped to [0, 1].

        Two `embed` calls per comparison and no caching, so scoring one question
        against N candidates is 2N calls. That cost is why `recall` tries exact
        matching first and only reaches a matcher when it misses.
        """
        a, b = self._embed(left), self._embed(right)
        dot = sum(x * y for x, y in zip(a, b))
        norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        if not norm:
            return 0.0
        # Cosine lands in [-1, 1]; the contract here is [0, 1] and a negative
        # similarity is "unrelated", not "oppositely related".
        return max(0.0, dot / norm)


#: What `SqliteRepository` uses unless a caller injects something else.
DEFAULT_MATCHER: SemanticMatcher = LexicalMatcher()
