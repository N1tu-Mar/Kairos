"""Evidence packs: the slice of the knowledge base a model call is shown.

The Drafter and Auditor used to receive every chunk the founder ever gave
us. Their prompts grew linearly with the knowledge base while a form asks a
dozen questions. An evidence pack is the deterministic subset that is
relevant to the fields in play, bounded in chunks and bytes.

What this is not:

*   **Not a summary.** Chunks are passed whole, with their ids and sources,
    exactly as stored. No model touches evidence here.
*   **Not the grounding check.** `guardrails.ship_gate` still runs against the
    full `KnowledgeBase`. A smaller prompt can only make the Drafter write
    less; it can never make the gate accept more.
*   **Not a change for small knowledge bases.** When the whole knowledge base
    fits inside the caps it is returned unchanged, so a founder with a
    typical deck sees byte-identical prompts.

Selection is lexical and deterministic: the same knowledge base and form
always produce the same pack, in the knowledge base's own order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent.models import ApplicationForm, Draft, KnowledgeBase, KnowledgeChunk

#: Most chunks one prompt may carry.
MAX_EVIDENCE_CHUNKS = 24
#: Most chunk-text bytes one prompt may carry (UTF-8).
MAX_EVIDENCE_BYTES = 16_000
#: Best-matching chunks considered per question before moving to the next.
PER_FIELD = 3

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    """
    a about above after all also an and any are as at be been before being
    but by can could describe did do does each for from had has have how i
    if in into is it its may more most must my no not of on one or our out
    over please provide should so such than that the their them then there
    these they this those to up us was we were what when where which who why
    will with would you your yours
    """.split()
)


def _terms(text: str) -> frozenset[str]:
    """Lowercase content words, crudely singularised."""
    out = set()
    for word in _WORD.findall(text.lower()):
        if len(word) < 3 or word in _STOP:
            continue
        out.add(word[:-1] if len(word) > 3 and word.endswith("s") else word)
    return frozenset(out)


def _chunk_terms(chunk: KnowledgeChunk) -> frozenset[str]:
    # The id and source carry the curator's own labels ("deck_traction",
    # "pitch_deck.pdf p.3"), which are often the best signal there is.
    return _terms(f"{chunk.text} {chunk.source} {chunk.chunk_id.replace('_', ' ')}")


@dataclass(frozen=True)
class EvidencePack:
    """The chunks a call is shown, and the numbers that describe the cut."""

    kb: KnowledgeBase
    total_chunks: int
    selected_chunks: int
    selected_bytes: int
    complete: bool


def _size(chunk: KnowledgeChunk) -> int:
    return len(chunk.text.encode("utf-8"))


def _fits_whole(kb: KnowledgeBase, max_chunks: int, max_bytes: int) -> bool:
    return len(kb.chunks) <= max_chunks and sum(map(_size, kb.chunks)) <= max_bytes


def _pack(kb: KnowledgeBase, chosen: set[str], complete: bool) -> EvidencePack:
    chunks = [c for c in kb.chunks if c.chunk_id in chosen]
    return EvidencePack(
        kb=KnowledgeBase(founder_id=kb.founder_id, chunks=chunks, traction=dict(kb.traction)),
        total_chunks=len(kb.chunks),
        selected_chunks=len(chunks),
        selected_bytes=sum(map(_size, chunks)),
        complete=complete,
    )


def select_evidence(
    kb: KnowledgeBase,
    queries: list[str],
    *,
    required_ids: list[str] | None = None,
    max_chunks: int = MAX_EVIDENCE_CHUNKS,
    max_bytes: int = MAX_EVIDENCE_BYTES,
    per_query: int = PER_FIELD,
) -> EvidencePack:
    """Pick the chunks relevant to `queries`, within the caps.

    1.  If the whole knowledge base fits, return it whole.
    2.  `required_ids` go in first, in the order given, while they fit.
    3.  Then round-robin across queries: each query's best match, then each
        query's second-best, up to `per_query`. Only chunks sharing at least
        one content word with the query are eligible — an unrelated chunk is
        never included to fill space.
    4.  Ties break on knowledge-base order. The result keeps that order.

    Structured traction is always carried; it is a handful of numbers.
    """
    if _fits_whole(kb, max_chunks, max_bytes):
        return _pack(kb, {c.chunk_id for c in kb.chunks}, complete=True)

    by_id = {c.chunk_id: c for c in kb.chunks}
    chosen: list[str] = []
    used = 0

    def take(chunk_id: str) -> None:
        nonlocal used
        chunk = by_id.get(chunk_id)
        if chunk is None or chunk_id in chosen or len(chosen) >= max_chunks:
            return
        if used + _size(chunk) > max_bytes:
            return
        chosen.append(chunk_id)
        used += _size(chunk)

    for chunk_id in required_ids or []:
        take(chunk_id)

    indexed = [(index, chunk, _chunk_terms(chunk)) for index, chunk in enumerate(kb.chunks)]
    ranked: list[list[str]] = []
    for query in queries:
        wanted = _terms(query)
        scored = sorted(
            (
                (-len(wanted & terms), index, chunk.chunk_id)
                for index, chunk, terms in indexed
                if wanted & terms
            )
        )
        ranked.append([chunk_id for _, _, chunk_id in scored[:per_query]])

    for rank in range(per_query):
        for matches in ranked:
            if rank < len(matches):
                take(matches[rank])

    return _pack(kb, set(chosen), complete=False)


def form_queries(form: ApplicationForm, field_ids: set[str]) -> list[str]:
    """One query per asked field: its id, label and help text, in form order."""
    return [
        f"{spec.field_id.replace('_', ' ')} {spec.label} {spec.help_text}"
        for spec in form.fields
        if spec.field_id in field_ids
    ]


def drafter_evidence(kb: KnowledgeBase, form: ApplicationForm, field_ids: set[str]) -> EvidencePack:
    """What the Drafter may cite for these fields."""
    return select_evidence(kb, form_queries(form, field_ids))


def auditor_evidence(kb: KnowledgeBase, draft: Draft, statuses: set[str]) -> EvidencePack:
    """What the Auditor checks answers against.

    Every chunk an answer cites goes in first, so a claim is never judged
    without the evidence its author relied on; then the chunks most related
    to each question and answer. The Auditor still never sees which field
    cited which chunk.
    """
    audited = [f for f in draft.fields if f.status in statuses]
    required = [span.chunk_id for f in audited for span in f.provenance]
    queries = [f"{f.field_id.replace('_', ' ')} {f.question} {f.answer or ''}" for f in audited]
    return select_evidence(kb, queries, required_ids=required)
