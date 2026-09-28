"""Evaluation Record contract: the single output format every evaluator (A, A+, B, K0, mocks) must emit.

Structural validation lives here (types, enums, unique ids). Semantic invariants that an evaluator can
violate while still producing well-formed JSON (gate precedence, dangling evidence references, verdict
vs evaluability mismatch, ...) are checked by contracts/record_checks.py so that the scorer can COUNT
them as integrity failures instead of discarding the whole record.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ignosis_eval.contracts._base import Contract, Id, NonEmptyStr, NonNegInt, Probability, Sha256Hex
from ignosis_eval.contracts.enums import (
    AttributionTarget,
    EvaluabilityStatus,
    EvaluatorArchitecture,
    EvidenceModality,
    GateStatus,
    InputMode,
    OutcomeClass,
    OutcomeCode,
    RepairStatus,
    RoutingDecision,
    Severity,
    Speaker,
    Verdict,
)
from ignosis_eval.versions import EVALUATION_RECORD_SCHEMA


class EvidenceItem(Contract):
    evidence_id: Id
    modality: EvidenceModality
    turn_ids: list[Id] = Field(default_factory=list)
    quote: str | None = None
    start_ms: NonNegInt | None = None
    end_ms: NonNegInt | None = None
    speaker: Speaker | None = None
    metadata_field: Literal["call_start_ts"] | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> "EvidenceItem":
        if self.modality is EvidenceModality.TRANSCRIPT and not self.turn_ids:
            raise ValueError(f"evidence {self.evidence_id}: transcript evidence requires turn_ids")
        if self.modality is EvidenceModality.AUDIO and (self.start_ms is None or self.end_ms is None):
            raise ValueError(f"evidence {self.evidence_id}: audio evidence requires start_ms and end_ms")
        if self.modality is EvidenceModality.METADATA and self.metadata_field is None:
            raise ValueError(f"evidence {self.evidence_id}: metadata evidence requires metadata_field")
        if self.start_ms is not None and self.end_ms is not None and self.start_ms > self.end_ms:
            raise ValueError(f"evidence {self.evidence_id}: start_ms > end_ms")
        return self


class GateResult(Contract):
    gate_id: Id
    status: GateStatus
    evidence_ids: list[Id] = Field(default_factory=list)
    confidence: Probability | None = None
    rationale: str | None = None


class DimensionResult(Contract):
    dimension_id: Id
    evaluable: bool
    score: float | None = None
    scale_min: float = 1.0
    scale_max: float = 5.0
    evidence_ids: list[Id] = Field(default_factory=list)
    confidence: Probability | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def _score(self) -> "DimensionResult":
        if self.scale_min >= self.scale_max:
            raise ValueError(f"dimension {self.dimension_id}: scale_min >= scale_max")
        if self.evaluable:
            if self.score is None:
                raise ValueError(f"dimension {self.dimension_id}: evaluable dimension requires a score")
            if not (self.scale_min <= self.score <= self.scale_max):
                raise ValueError(f"dimension {self.dimension_id}: score outside scale")
        elif self.score is not None:
            raise ValueError(f"dimension {self.dimension_id}: non-evaluable dimension must not carry a score")
        return self


class AttributionClaim(Contract):
    target: AttributionTarget
    confidence: Probability | None = None
    evidence_ids: list[Id] = Field(default_factory=list)
    rationale: str | None = None


class Finding(Contract):
    finding_id: Id
    defect_id: Id
    severity: Severity
    gate_id: Id | None = None
    dimension_id: Id | None = None
    repair_status: RepairStatus = RepairStatus.NOT_REPAIRED
    evidence_ids: list[Id] = Field(default_factory=list)
    attribution: AttributionClaim
    confidence: Probability | None = None
    description: str | None = None


class EvaluabilityAssessment(Contract):
    status: EvaluabilityStatus
    reasons: list[str] = Field(default_factory=list)
    rationale: str | None = None


class ConfidenceSummary(Contract):
    overall: Probability | None = None
    method: Literal["self_reported", "heuristic", "calibrated", "none"] = "none"
    calibrated: bool = False


class ObservableOutcomes(Contract):
    outcomes: list[OutcomeCode] = Field(default_factory=list)
    outcome_class: OutcomeClass = OutcomeClass.UNKNOWN
    evidence_ids: list[Id] = Field(default_factory=list)


class Routing(Contract):
    decision: RoutingDecision
    reasons: list[str] = Field(default_factory=list)


class EvaluatorInfo(Contract):
    name: NonEmptyStr
    version: NonEmptyStr
    architecture: EvaluatorArchitecture
    model_id: str | None = None
    prompt_hashes: dict[str, Sha256Hex] = Field(default_factory=dict)
    config_hash: Sha256Hex | None = None


class ExperimentMetadata(Contract):
    """Attached by the experiment runner after evaluation; evaluators leave this null."""

    run_id: NonEmptyStr
    item_id: NonEmptyStr
    repetition: int = Field(ge=1)
    seed: int
    rep_seed: int
    started_at: AwareDatetime
    finished_at: AwareDatetime


class EvaluationRecord(Contract):
    schema_version: Literal["evaluation_record/1.0.0"] = EVALUATION_RECORD_SCHEMA
    record_id: Id
    call_id: Id
    evaluator: EvaluatorInfo
    rubric_version: NonEmptyStr
    profile_id: NonEmptyStr
    profile_version: NonEmptyStr
    profile_sha256: Sha256Hex | None = None
    input_mode: InputMode
    evaluability: EvaluabilityAssessment
    verdict: Verdict
    gates: list[GateResult] = Field(default_factory=list)
    dimensions: list[DimensionResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: ConfidenceSummary = Field(default_factory=ConfidenceSummary)
    primary_attribution: AttributionClaim | None = None
    observable_outcomes: ObservableOutcomes | None = None
    dangerous_win: bool | None = None
    clean_loss: bool | None = None
    routing: Routing
    experiment: ExperimentMetadata | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> "EvaluationRecord":
        for label, ids in (
            ("evidence_id", [e.evidence_id for e in self.evidence]),
            ("finding_id", [f.finding_id for f in self.findings]),
            ("gate_id", [g.gate_id for g in self.gates]),
            ("dimension_id", [d.dimension_id for d in self.dimensions]),
        ):
            dupes = {i for i in ids if ids.count(i) > 1}
            if dupes:
                raise ValueError(f"duplicate {label}s: {sorted(dupes)}")
        return self

    # --- helpers -----------------------------------------------------------------------------------
    def gate(self, gate_id: str) -> GateResult | None:
        return next((g for g in self.gates if g.gate_id == gate_id), None)

    def evidence_map(self) -> dict[str, EvidenceItem]:
        return {e.evidence_id: e for e in self.evidence}

    def content_hash(self) -> str:
        """Hash of the evaluator-determined content (excludes runner-attached experiment metadata).

        Two runs with identical inputs, seeds and evaluator configuration must produce identical
        content hashes; this is the basis of the run-reproducibility test.
        """
        payload = self.model_dump(mode="json", exclude={"experiment"})
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
