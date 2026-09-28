"""Gold Label Record contract — kept completely separate from evaluator output.

Rules enforced here:
  * `provenance.derived_from_evaluator_output` is the literal `false`: a gold label produced from an
    evaluator output cannot be represented.
  * Labelers must declare whether they saw evaluator outputs; for the holdout split, nobody may have.
  * Gold obeys the same verdict/evaluability invariant and gate precedence as evaluator records.
  * Contested-unresolved labels cannot claim HIGH confidence.

Evaluator code must never import this module (enforced by tests/test_architecture_boundaries.py) and
the evaluator process is denied filesystem access to the gold directory (integrity/guard.py).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ignosis_eval.contracts._base import CaseId, Contract, Id, NonEmptyStr, NonNegInt, Sha256Hex
from ignosis_eval.contracts.enums import (
    EVALUABILITY_TO_VERDICTS,
    AdjudicationMethod,
    AttributionTarget,
    ContestedStatus,
    EvaluabilityStatus,
    GateStatus,
    GoldSource,
    LabelConfidence,
    LabelerRole,
    OutcomeClass,
    OutcomeCode,
    RepairStatus,
    Severity,
    Split,
    Verdict,
)
from ignosis_eval.versions import GOLD_LABEL_SCHEMA


class ExpectedEvidence(Contract):
    required_turn_ids: list[Id] = Field(
        default_factory=list, description="Turns an evaluator must cite for its evidence to be complete."
    )
    acceptable_turn_ids: list[Id] = Field(
        default_factory=list, description="Additional turns that are on-target if cited."
    )
    audio_spans_ms: list[tuple[NonNegInt, NonNegInt]] = Field(default_factory=list)
    metadata_fields: list[Literal["call_start_ts"]] = Field(default_factory=list)
    notes: str | None = None


class ExpectedGate(Contract):
    gate_id: Id
    status: GateStatus
    acceptable_statuses: list[GateStatus] = Field(default_factory=list)

    def accepts(self, status: GateStatus | None) -> bool:
        return status is not None and (status == self.status or status in self.acceptable_statuses)


class ExpectedDefect(Contract):
    defect_id: Id
    severity: Severity
    gate_id: Id | None = None
    repair_status: RepairStatus = RepairStatus.NOT_REPAIRED
    required: bool = Field(
        default=True,
        description="True: the evaluator must detect it (counts toward recall/misses). "
        "False: detection is acceptable but not required.",
    )
    evidence: ExpectedEvidence = Field(default_factory=ExpectedEvidence)
    attribution: AttributionTarget
    attribution_determinable: bool

    @model_validator(mode="after")
    def _attr(self) -> "ExpectedDefect":
        if self.attribution_determinable == (self.attribution is AttributionTarget.UNDETERMINED):
            raise ValueError(
                f"defect {self.defect_id}: attribution_determinable must be false iff attribution is 'undetermined'"
            )
        return self


class ExpectedEvaluability(Contract):
    status: EvaluabilityStatus
    reasons: list[str] = Field(default_factory=list)


class ExpectedOutcome(Contract):
    outcome_class: OutcomeClass
    outcomes: list[OutcomeCode] = Field(default_factory=list)


class Contested(Contract):
    status: ContestedStatus = ContestedStatus.UNCONTESTED
    notes: str | None = None
    alternative_verdicts: list[Verdict] = Field(default_factory=list)


class GoldProvenance(Contract):
    source: GoldSource
    case_card_ref: str | None = None
    case_card_sha256: Sha256Hex | None = None
    labeling_protocol_version: NonEmptyStr
    created_at: AwareDatetime
    derived_from_evaluator_output: Literal[False] = False
    notes: str | None = None


class LabelerRecord(Contract):
    labeler_id: NonEmptyStr = Field(description="Pseudonymous labeler identifier (no personal data).")
    role: LabelerRole
    labeled_at: AwareDatetime
    blind_to_case_card: bool
    saw_evaluator_outputs: bool
    expertise: str | None = None


class Adjudication(Contract):
    required: bool
    adjudicator_id: str | None = None
    adjudicated_at: AwareDatetime | None = None
    method: AdjudicationMethod | None = None
    disagreements: list[str] = Field(default_factory=list)
    resolution_notes: str | None = None

    @model_validator(mode="after")
    def _complete(self) -> "Adjudication":
        if self.required and not (self.adjudicator_id and self.adjudicated_at and self.method):
            raise ValueError("adjudication.required=true needs adjudicator_id, adjudicated_at and method")
        return self


class GoldLabel(Contract):
    schema_version: Literal["gold_label/1.0.0"] = GOLD_LABEL_SCHEMA
    item_id: CaseId
    split: Split
    scenario: NonEmptyStr
    expected_evaluability: ExpectedEvaluability
    expected_verdict: Verdict
    acceptable_verdicts: list[Verdict] = Field(default_factory=list)
    expected_gates: list[ExpectedGate] = Field(default_factory=list)
    expected_defects: list[ExpectedDefect] = Field(default_factory=list)
    acceptable_extra_defects: list[Id] = Field(
        default_factory=list,
        description="Defect ids that are defensible if an evaluator raises them (not counted as unsupported).",
    )
    expected_primary_attribution: AttributionTarget | None = None
    expected_outcome: ExpectedOutcome | None = None
    expected_dangerous_win: bool | None = None
    expected_clean_loss: bool | None = None
    confidence: LabelConfidence
    contested: Contested = Field(default_factory=Contested)
    provenance: GoldProvenance
    labelers: list[LabelerRecord] = Field(min_length=1)
    adjudication: Adjudication | None = None

    @model_validator(mode="after")
    def _invariants(self) -> "GoldLabel":
        errs: list[str] = []
        allowed = EVALUABILITY_TO_VERDICTS[self.expected_evaluability.status]
        if self.expected_verdict not in allowed:
            errs.append(
                f"expected_verdict={self.expected_verdict} inconsistent with "
                f"expected_evaluability={self.expected_evaluability.status}"
            )
        if any(g.status is GateStatus.FAIL for g in self.expected_gates) and self.expected_verdict is not Verdict.FAIL:
            errs.append("gate precedence: an expected gate FAIL requires expected_verdict=fail")
        gate_ids = [g.gate_id for g in self.expected_gates]
        if len(gate_ids) != len(set(gate_ids)):
            errs.append("duplicate gate_id in expected_gates")
        defect_ids = [d.defect_id for d in self.expected_defects]
        if len(defect_ids) != len(set(defect_ids)):
            errs.append("duplicate defect_id in expected_defects (fold repeated instances into one defect)")
        overlap = set(defect_ids) & set(self.acceptable_extra_defects)
        if overlap:
            errs.append(f"defects listed both as expected and acceptable_extra: {sorted(overlap)}")
        if self.expected_dangerous_win and self.expected_clean_loss:
            errs.append("expected_dangerous_win and expected_clean_loss cannot both be true")
        if (
            self.contested.status is ContestedStatus.CONTESTED_UNRESOLVED
            and self.confidence is LabelConfidence.HIGH
        ):
            errs.append("contested_unresolved labels cannot have HIGH confidence")
        if self.split is Split.HOLDOUT and any(lb.saw_evaluator_outputs for lb in self.labelers):
            errs.append("holdout gold must be labeled blind to evaluator outputs")
        if self.provenance.source is GoldSource.ADJUDICATED and not (self.adjudication and self.adjudication.required):
            errs.append("provenance.source=adjudicated requires a completed adjudication block")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    def accepts_verdict(self, verdict: Verdict | None) -> bool:
        return verdict is not None and (verdict == self.expected_verdict or verdict in self.acceptable_verdicts)

    def expected_gate(self, gate_id: str) -> ExpectedGate | None:
        return next((g for g in self.expected_gates if g.gate_id == gate_id), None)

    @property
    def all_expected_defect_ids(self) -> set[str]:
        return {d.defect_id for d in self.expected_defects}
