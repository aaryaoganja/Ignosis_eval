"""Case Card contract: the human-authored specification of one benchmark case.

The case card records AUTHOR INTENT (what the case is designed to test). It is not the gold label: gold
is a separate, independently validated record (contracts/gold_label.py) that may cite the card.
Case cards reveal the answer key and are therefore protected from the evaluator process.

Structural requirements are enforced here; semantic authoring rules (with rule ids) are enforced by
ignosis_eval/benchmark/case_card_rules.py.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field

from ignosis_eval.contracts._base import CaseId, Contract, Id, LanguageTag, NonEmptyStr
from ignosis_eval.contracts.benchmark import CapabilityBoundaries, CaseMetadata
from ignosis_eval.contracts.enums import (
    AttributionTarget,
    EvaluabilityStatus,
    InputMode,
    OutcomeClass,
    OutcomeCode,
    RepairStatus,
    SourceKind,
    Split,
)
from ignosis_eval.versions import CASE_CARD_SCHEMA


class CardStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"


class CardSeverity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    NONE = "none"


class Authoring(Contract):
    author_id: NonEmptyStr
    created_on: date
    reviewers: list[str] = Field(default_factory=list)


class CustomerContext(Contract):
    persona: NonEmptyStr
    account_context: NonEmptyStr  # narrative only; no real account data
    situation: NonEmptyStr
    vulnerability_flags: list[str] = Field(default_factory=list)


class TargetDefect(Contract):
    defect_id: Id
    description: NonEmptyStr


class IncidentalDefect(Contract):
    defect_id: Id
    severity: CardSeverity
    description: NonEmptyStr


class CardAttribution(Contract):
    target: AttributionTarget
    determinable: bool
    rationale: NonEmptyStr


class CardOutcome(Contract):
    outcome_class: OutcomeClass
    outcomes: list[OutcomeCode] = Field(default_factory=list)
    notes: str | None = None


class CardModality(Contract):
    intended_modality: InputMode
    constraints: list[str] = Field(default_factory=list)
    capability_boundaries: CapabilityBoundaries


class MinimalPair(Contract):
    pair_id: Id
    role: NonEmptyStr
    counterpart_case_id: CaseId
    manipulated_factor: NonEmptyStr
    held_constant: list[NonEmptyStr]


class AttributionPair(Contract):
    attribution_pair_id: Id
    counterpart_case_ids: list[CaseId]
    manipulated_cause: NonEmptyStr


class JudgeBait(Contract):
    kind: NonEmptyStr
    description: NonEmptyStr


class CaseCard(Contract):
    case_card_version: Literal["case_card/1.0.0"] = CASE_CARD_SCHEMA
    case_id: CaseId
    status: CardStatus
    authoring: Authoring
    split: Split
    scenario: NonEmptyStr
    category: NonEmptyStr
    language: LanguageTag
    source_kind: SourceKind
    rationale: NonEmptyStr
    customer_context: CustomerContext
    intended_behavior: NonEmptyStr
    agent_behavior: NonEmptyStr
    target_defect: TargetDefect | None
    gate: Id | None
    severity: CardSeverity
    repair_status: RepairStatus
    evidence_turns: list[Id]
    evidence_metadata_fields: list[Literal["call_start_ts"]] = Field(default_factory=list)
    evidence_audio_spans_ms: list[tuple[int, int]] = Field(default_factory=list)
    attribution: CardAttribution | None
    incidental_defects: list[IncidentalDefect] = Field(default_factory=list)
    outcome: CardOutcome
    dangerous_win: bool
    clean_loss: bool
    modality: CardModality
    expected_evaluability: EvaluabilityStatus
    ambiguity_notes: NonEmptyStr
    minimal_pair: MinimalPair | None
    attribution_pair: AttributionPair | None
    judge_bait: JudgeBait | None
    tags: list[str] = Field(default_factory=list)

    def to_metadata(self) -> CaseMetadata:
        return CaseMetadata(
            case_id=self.case_id,
            split=self.split,
            scenario=self.scenario,
            category=self.category,
            intended_modality=self.modality.intended_modality,
            language=self.language,
            synthetic=self.source_kind.is_synthetic,
            source_kind=self.source_kind,
            pair_id=self.minimal_pair.pair_id if self.minimal_pair else None,
            pair_role=self.minimal_pair.role if self.minimal_pair else None,
            attribution_pair_id=self.attribution_pair.attribution_pair_id if self.attribution_pair else None,
            judge_bait=self.judge_bait is not None,
            judge_bait_kind=self.judge_bait.kind if self.judge_bait else None,
            capability_boundaries=self.modality.capability_boundaries,
            tags=list(self.tags),
        )
