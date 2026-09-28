"""Case Card — authoring contract for B-01 (case content is written by the human case author, never by the
implementing agent). Vocabulary reconciled with rubric 1.0-mvp; ids are canonical bench-a1 ids (R-07).

The card records author intent (scenario, target behavior, intended labels, pair membership). Gold is a
separate, blind-labeled record (contracts/gold_label.py).
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field

from ignosis_eval.contracts._base import Contract, ItemId, NonEmptyStr
from ignosis_eval.contracts.enums import (
    DangerousWin,
    Disposition,
    EvaluabilityStatus,
    GateId,
    Pack,
    ReasonCode,
    RepairStatus,
    Severity,
    Split,
    UnitMode,
)
from ignosis_eval.contracts.gold_label import AttributionFacts
from ignosis_eval.versions import CASE_CARD_SCHEMA


class CardStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"


class Authoring(Contract):
    author_id: NonEmptyStr
    created_on: date
    reviewers: list[str] = Field(default_factory=list)
    llm_assisted: bool = False
    llm_family: str | None = None  # B-01 rule 1: must differ from the evaluator's model family


class CardOutcome(Contract):
    dispositions: list[Disposition] = Field(default_factory=list)
    positive: bool = False  # AJ-09: PTP_STATED is positive only if firm (full or partial amount)


class PairMembership(Contract):
    pair_id: NonEmptyStr  # e.g. MP-01, CP-01 (frozen-contract §12)
    role: Literal["clean", "violating"]
    counterpart_item_id: ItemId


class CaseCard(Contract):
    case_card_version: Literal["case_card/2.1.0"] = CASE_CARD_SCHEMA
    item_id: ItemId
    status: CardStatus
    authoring: Authoring
    split: Split
    pack: Pack
    language: Literal["en", "hi", "hi-en", "other"]
    unit_modes: list[UnitMode] = Field(min_length=1)
    intent: NonEmptyStr  # one-line intent (frozen-contract §12)
    rationale: NonEmptyStr
    scenario: NonEmptyStr
    target_behavior: NonEmptyStr
    target_check: str | None  # gate id or MVP code, or null for a clean case
    target_sub_rule: str | None = None
    severity: Severity | None  # post-repair; null for a clean case
    repair_status: RepairStatus | None
    evidence_turns: list[int] = Field(default_factory=list)
    evidence_header: bool = False  # G7 evidence is the header call_start_ts
    attribution_facts: AttributionFacts = Field(default_factory=AttributionFacts)
    outcome: CardOutcome = Field(default_factory=CardOutcome)
    dangerous_win: DangerousWin = DangerousWin.NONE
    clean_loss: bool = False
    expected_evaluability: EvaluabilityStatus = EvaluabilityStatus.EVALUABLE
    expected_reason_codes: list[ReasonCode] = Field(default_factory=list)
    pair: PairMembership | None = None
    control_target_gates: list[GateId] = Field(default_factory=list)
    twin_of: ItemId | None = None
    ambiguity_notes: NonEmptyStr
    tags: list[str] = Field(default_factory=list)
