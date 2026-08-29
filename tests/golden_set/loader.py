"""Case files, and the schema they have to satisfy.

A case is data, not code, so that "half of these contain deliberate traps"
is auditable by reading `cases/` rather than by trusting this docstring.
Every case declares, per field, what the *correct* outcome is — and that
declaration is written by hand from the knowledge base, never derived from
what the code under test does. A scorer that asks the gate whether the gate
was right is marking its own homework.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.models import (
    ApplicationField,
    ApplicationForm,
    FieldAudit,
    KnowledgeBase,
    KnowledgeChunk,
    Opportunity,
)
from agent.subagents.drafter import DraftProposal, ProposedField

CASES_DIR = Path(__file__).resolve().parent / "cases"

#: What should happen to a field, decided by a human reading the knowledge base.
#:
#: SHIP     — the claim is supported by the material. Withholding it costs the
#:            founder a question they should not have had to answer.
#: WITHHOLD — the claim is not supported, or the field is one only the founder
#:            may answer. It must not reach a real application.
Truth = Literal["SHIP", "WITHHOLD"]


class Case(BaseModel):
    """One golden-set case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    #: Whether this case contains a deliberate trap. Counted, not trusted:
    #: `python -m tests.golden_set.loader` prints the real ratio.
    trap: bool
    why: str

    chunks: list[KnowledgeChunk] = Field(default_factory=list)
    traction: dict[str, float] = Field(default_factory=dict)
    opportunity: Opportunity
    fields: list[ApplicationField]

    #: What the Drafter proposed. Offline this is the fixture; with `--live`
    #: it is replaced by whatever Bedrock actually returns.
    proposal: list[ProposedField]

    #: What the Auditor proposed. Absent means the permissive default below —
    #: an auditor that waves everything through, so the deterministic checks
    #: are measured on their own.
    auditor: list[FieldAudit] | None = None

    truth: dict[str, Truth]

    def knowledge_base(self, founder_id: str) -> KnowledgeBase:
        """The closed world for this case, bound to a founder id.

        Copies both collections, so a case can be run twice without the first run
        having mutated it.
        """
        return KnowledgeBase(
            founder_id=founder_id, chunks=list(self.chunks), traction=dict(self.traction)
        )

    def form(self) -> ApplicationForm:
        """The case's fields as an `ApplicationForm`, named after the opportunity."""
        return ApplicationForm(
            opportunity_id=self.opportunity.id,
            name=f"{self.opportunity.title} application",
            source_url=self.opportunity.source_url,
            fields=list(self.fields),
        )

    def draft_proposal(self) -> DraftProposal:
        """The fixture Drafter output, as the model the real pipeline would receive.

        Offline this is what the case file states. With `--live` it is discarded
        and replaced by whatever Bedrock returns — the same scorer runs over
        both, which is what makes the two modes comparable.
        """
        return DraftProposal(fields=list(self.proposal))

    def permissive_audit(self) -> list[FieldAudit]:
        """The default Auditor: SUPPORTED, with a quote lifted from the KB.

        Deliberately the worst realistic auditor rather than a good one. The
        ship gate exists to catch what an auditor missed, so an eval whose
        auditor catches the traps first would be scoring the auditor and
        reporting it as the gate.
        """
        quote = self.chunks[0].text if self.chunks else "unavailable"
        return [
            FieldAudit(
                field_id=p.field_id,
                verdict="SUPPORTED",
                supporting_quote=quote,
                note="permissive default auditor",
            )
            for p in self.proposal
            if p.status in {"GENERATED", "KNOWN", "REUSED"}
        ]

    def audit_fields(self) -> list[FieldAudit]:
        """The case's own auditor if it states one, otherwise the permissive default.

        A case that states an auditor is testing what happens when the auditor
        catches something; every other case measures the gate with the auditor
        deliberately unhelpful.
        """
        return self.auditor if self.auditor is not None else self.permissive_audit()


@dataclass(frozen=True)
class CaseSet:
    """The loaded cases, split into traps and clean cases.

    `trap` is a per-case declaration, so the split is only as honest as the
    case files. `describe()` prints the real counts rather than the intended
    ratio, which is how a drift toward all-traps or all-clean is noticed.
    """

    cases: list[Case]

    @property
    def traps(self) -> list[Case]:
        """Cases containing a deliberate trap — something the gate must catch."""
        return [c for c in self.cases if c.trap]

    @property
    def clean(self) -> list[Case]:
        """Cases with nothing to catch. These measure over-withholding, which is the cost side of the gate."""
        return [c for c in self.cases if not c.trap]

    def describe(self) -> str:
        """One line of counts, for the eval header."""
        return (
            f"{len(self.cases)} cases: {len(self.traps)} with traps, "
            f"{len(self.clean)} clean"
        )


def load_cases(directory: Path | None = None) -> CaseSet:
    """Every case in `cases/`, sorted by filename so runs are reproducible."""
    directory = directory or CASES_DIR
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"no golden-set cases found in {directory}")

    cases: list[Case] = []
    seen: set[str] = set()
    for path in paths:
        case = Case.model_validate(json.loads(path.read_text()))
        if case.case_id in seen:
            raise ValueError(f"duplicate case_id {case.case_id!r} in {path.name}")
        seen.add(case.case_id)

        declared = set(case.truth)
        on_form = {f.field_id for f in case.fields}
        if declared != on_form:
            raise ValueError(
                f"{path.name}: truth covers {sorted(declared)} but the form has "
                f"{sorted(on_form)}. Every field needs a declared outcome — a "
                f"field with no ground truth is a field nobody scored."
            )
        cases.append(case)

    return CaseSet(cases=cases)


if __name__ == "__main__":  # pragma: no cover
    print(load_cases().describe())
