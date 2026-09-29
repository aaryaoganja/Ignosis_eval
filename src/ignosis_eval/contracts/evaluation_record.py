"""Evaluation Record — the output contract of every system (K0, A, A+, B).

Reconciled with frozen-contract.md §5/§9/§10, rubric.yaml (enums, verdict_rules, tags, routing) and
scoring-spec.md SD-02 (an OK record must contain every gate G1–G9; unknown enum values are schema
errors, which the scorer treats as EVALUATION_FAILED).

Structural rules are validated here. Semantic rules that a raw LLM output (Evaluator A) may violate
are NOT enforced by the schema, so they can be measured (e.g. H6 capability violations).
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ignosis_eval.contracts._base import Contract, NonEmptyStr
from ignosis_eval.contracts.enums import (
    GATE_IDS,
    ActionType,
    Attribution,
    AttributionBasis,
    CheckStatus,
    Confidence,
    ConfidenceSource,
    CriticalStatus,
    DangerousWin,
    DimensionStatus,
    Disposition,
    EvaluabilityStatus,
    Firmness,
    FindingState,
    GateId,
    GateStatus,
    InputMode,
    MeasurementBasis,
    OosReason,
    OutcomeAttribution,
    ReasonCode,
    RecordStatus,
    RepairStatus,
    Role,
    Severity,
    UnitMode,
    Verdict,
)
from ignosis_eval.versions import EVALUATION_RECORD_SCHEMA


class Evidence(Contract):
    """A cited span. `turn` is the 1-based turn number of the normalized input (SD-12 anchors use it)."""

    turn: int | None = Field(default=None, ge=1)
    quote: str | None = None
    role: Role | None = None
    source: Literal["supplied", "asr"] | None = None  # SD-13 reference text selector
    element: str | None = None  # rubric required_evidence_elements name, if known
    header_field: Literal["call_start_ts"] | None = None  # G7 evidence comes from the header

    @model_validator(mode="after")
    def _shape(self) -> "Evidence":
        if self.header_field is None:
            if self.turn is None or self.quote is None or self.role is None:
                raise ValueError("turn evidence requires turn, quote and role")
        elif self.turn is not None:
            raise ValueError("header evidence must not cite a turn")
        return self


class AttributionResult(Contract):
    primary: Attribution
    secondary: Attribution | None = None
    basis: AttributionBasis
    notes: list[str] = Field(default_factory=list)  # e.g. "perception_plausible"


class GateResult(Contract):
    gate: GateId
    status: GateStatus
    critical_status: CriticalStatus | None = None
    confidence: Confidence | None = None
    sub_rule: str | None = None
    in_span_trigger: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    attribution: AttributionResult | None = None
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    oos_reason: OosReason | None = None
    evidence_unverified: bool = False
    note: str | None = None

    @model_validator(mode="after")
    def _critical(self) -> "GateResult":
        if (self.status is GateStatus.FAIL) != (self.critical_status is not None):
            raise ValueError(f"{self.gate}: critical_status is required iff status is FAIL")
        return self


class Finding(Contract):
    code: NonEmptyStr
    sub_rule: str | None = None
    severity: Severity  # post-repair
    repair_status: RepairStatus = RepairStatus.UNREPAIRED
    finding_state: FindingState = FindingState.ASSERTED
    confidence: Confidence
    action_type: ActionType
    attribution: AttributionResult
    evidence: list[Evidence] = Field(min_length=1)
    anchor_turn: int | None = Field(default=None, ge=1)
    evidence_unverified: bool = False
    measurement_basis: MeasurementBasis | None = None  # TRT-06 only (AJ-02)
    description: str | None = None  # English only (P-3)

    @model_validator(mode="after")
    def _basis(self) -> "Finding":
        if self.measurement_basis is not None and self.code != "TRT-06":
            raise ValueError(f"{self.code}: measurement_basis is recorded only on TRT-06 findings (AJ-02)")
        return self


class CheckStatusEntry(Contract):
    """Explicit non-default status of a non-gate check (NA / INCONCLUSIVE / OUT_OF_SCOPE).

    SD-02: a code absent from the record means "not emitted" (PASS). A DEFECT is expressed by an
    ASSERTED finding, not by an entry here.
    """

    code: NonEmptyStr
    status: Literal[CheckStatus.NA, CheckStatus.INCONCLUSIVE, CheckStatus.OUT_OF_SCOPE]
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    oos_reason: OosReason | None = None


class EvaluabilityResult(Contract):
    status: EvaluabilityStatus
    reason_codes: list[ReasonCode] = Field(default_factory=list)


class Outcome(Contract):
    dispositions: list[Disposition] = Field(default_factory=list)
    positive: bool = False
    commitment_turn: int | None = Field(default=None, ge=1)
    firmness: Firmness | None = None
    outcome_attribution: OutcomeAttribution | None = None
    verified: dict | None = None  # must stay null: verified outcomes are OUT_OF_SCOPE (SD-11 b)


class Tags(Contract):
    dangerous_win: DangerousWin = DangerousWin.NONE
    clean_loss: bool = False
    high_friction: bool | None = None  # null while the threshold is PENDING (B-14)
    coverage: Literal["conversation_only"] = "conversation_only"


class OutOfScopeEntry(Contract):
    code: NonEmptyStr
    oos_reason: OosReason


class RecordBody(Contract):
    """Everything a system asserts about a unit. Evaluator A's LLM emits exactly this structure."""

    verdict: Verdict | None = None
    critical_status: CriticalStatus | None = None
    within_scope_complete: bool | None = None
    evaluability: EvaluabilityResult | None = None
    gates: list[GateResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    checks: list[CheckStatusEntry] = Field(default_factory=list)
    dimensions: dict[str, DimensionStatus] = Field(default_factory=dict)
    outcome: Outcome | None = None
    tags: Tags | None = None
    routing_tier: int | None = Field(default=None, ge=1, le=6)
    out_of_scope: list[OutOfScopeEntry] = Field(default_factory=list)
    unverified_agent_commitments: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _body(self) -> "RecordBody":
        ids = [g.gate for g in self.gates]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate gate entries")
        codes = [c.code for c in self.checks]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate check entries")
        return self

    def gate(self, gate_id: str) -> GateResult | None:
        return next((g for g in self.gates if g.gate == gate_id), None)


class SystemInfo(Contract):
    system: NonEmptyStr  # K0 | A | A+ | B, or a blind alias SYS-n in the blinded scoring view
    version: NonEmptyStr
    llm_backend: str | None = None  # the provider of the LLM calls (e.g. gemini); null without one (3.1.0)
    model_snapshot_id: str | None = None
    prompt_hashes: dict[str, str] = Field(default_factory=dict)


class FailureInfo(Contract):
    reason: NonEmptyStr
    schema_attempts: int = Field(ge=0)


class ExperimentMeta(Contract):
    """Attached by the runner; systems leave this null."""

    run_id: NonEmptyStr
    item_id: NonEmptyStr
    unit_mode: UnitMode
    repetition: int = Field(ge=1)
    base_seed: int
    started_at: AwareDatetime
    finished_at: AwareDatetime


class EvaluationRecord(RecordBody):
    schema_version: Literal["evaluation_record/3.1.0"] = EVALUATION_RECORD_SCHEMA
    record_status: RecordStatus
    confidence_source: ConfidenceSource | None = None  # AJ-06: A = SELF_REPORTED; A+, B, K0 = COMPUTED
    system: SystemInfo
    contract_version: NonEmptyStr
    rubric_version: NonEmptyStr
    profile_id: NonEmptyStr
    profile_version: NonEmptyStr
    input_mode: InputMode
    unit_mode: UnitMode
    failure: FailureInfo | None = None
    experiment: ExperimentMeta | None = None

    @model_validator(mode="after")
    def _status(self) -> "EvaluationRecord":
        if self.record_status is RecordStatus.EVALUATION_FAILED:
            if self.verdict is not None or self.within_scope_complete is not None:
                raise ValueError("EVALUATION_FAILED records carry verdict=null (V0)")
            if self.failure is None:
                raise ValueError("EVALUATION_FAILED records must state a failure reason")
            return self
        if self.verdict is None or self.within_scope_complete is None or self.evaluability is None:
            raise ValueError("OK records require verdict, within_scope_complete and evaluability")
        if self.confidence_source is None:
            raise ValueError("OK records state their confidence_source (AJ-06)")
        present = {g.gate for g in self.gates}
        missing = [g.value for g in GATE_IDS if g not in present]
        if missing:
            raise ValueError(f"OK record is missing gates {missing} (SD-02: every gate must be present)")
        if (self.verdict is Verdict.CRITICAL_FAIL) != (self.critical_status is not None):
            raise ValueError("critical_status is required iff verdict is CRITICAL_FAIL")
        if self.failure is not None:
            raise ValueError("OK records must not carry a failure block")
        return self

    def content_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"experiment"})
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
