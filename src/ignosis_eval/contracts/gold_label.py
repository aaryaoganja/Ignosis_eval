"""Gold Label — content-level gold per item (scoring-spec SD-01). Completely separate from evaluator output.

Mode-level gold is NOT stored: it is derived by code (ignosis_eval/golddrv) from content gold plus the
capability table (frozen-contract §8), per SD-01 / P-12.

Isolation rules kept from the infrastructure phase (they do not conflict with the spec and implement
B-02 "blind labeling"): gold can never declare it was derived from an evaluator output, and holdout /
red-team labelers must be blind to evaluator outputs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ignosis_eval.contracts._base import Contract, ItemId, NonEmptyStr, Sha256Hex
from ignosis_eval.contracts.enums import (
    GATE_IDS,
    DangerousWin,
    Disposition,
    GateId,
    GateStatus,
    RepairStatus,
    Severity,
    Split,
    Verdict,
)
from ignosis_eval.versions import GOLD_LABEL_SCHEMA

# SD-01: implicit gold for an MVP code not present anywhere is PASS with label_confidence = "Sure".
# The full label_confidence vocabulary belongs to the labeling protocol (B-10, pending); only the value
# the scorer relies on is fixed here.
LABEL_CONFIDENCE_SURE = "Sure"
# rubric.yaml › repair_rules.allowlist (AJ-08); a drift test keeps this equal to the rubric.
REPAIRABLE_CODES = frozenset({"ACC-05"})


class GoldGate(Contract):
    status: GateStatus
    trigger: bool | None = None  # required iff status is INCONCLUSIVE (INCONCLUSIVE(trigger: Y/N))
    contested: bool = False
    anchor_turns: list[int] = Field(default_factory=list)  # G7 anchors on the header: leave empty
    label_confidence: NonEmptyStr = LABEL_CONFIDENCE_SURE

    @model_validator(mode="after")
    def _trigger(self) -> "GoldGate":
        if (self.status is GateStatus.INCONCLUSIVE) != (self.trigger is not None):
            raise ValueError("trigger must be set iff the gold gate status is INCONCLUSIVE")
        if any(t < 1 for t in self.anchor_turns):
            raise ValueError("anchor turns are 1-based")
        return self


class AttributionFacts(Contract):
    """Content facts from which the gold-derivation module derives the expected attribution per mode.

    The spec names `attribution_facts` (SD-01) without enumerating fields; these are exactly the inputs
    the rubric's attribution rules need (registration override, perception test, read-back safeguard).
    """

    registered: bool | None = None  # an agent event responds_to the trigger (non_response override)
    said_heard_material_difference: bool | None = None  # SAID != HEARD on the trigger span (audio items)
    readback_safeguard_present: bool | None = None  # for the secondary AGENT_BEHAVIOR on critical entities
    notes: str | None = None


class GoldFinding(Contract):
    """A gold defect. `code` may be a non-gate MVP code, a PLT code, or a gate id (to carry the gate's
    evidence elements and attribution facts for SD-14 / SD-16; the gate status itself lives in `gates`)."""

    code: NonEmptyStr
    anchor_turns: list[int] = Field(min_length=1)
    severity: Severity  # post-repair
    repair_status: RepairStatus = RepairStatus.UNREPAIRED
    evidence_elements: dict[str, int] = Field(default_factory=dict)
    attribution_facts: AttributionFacts = Field(default_factory=AttributionFacts)
    label_confidence: NonEmptyStr = LABEL_CONFIDENCE_SURE


class GoldVerdict(Contract):
    value: Verdict
    within_scope_complete: bool


class GoldOutcome(Contract):
    dispositions: list[Disposition] = Field(default_factory=list)
    positive: bool = False  # AJ-09: PTP_STATED is positive only if firm (full or partial amount); labeler applies it


class GoldTags(Contract):
    dangerous_win: DangerousWin = DangerousWin.NONE
    clean_loss: bool = False


class AbstentionTarget(Contract):
    check: NonEmptyStr  # a gate id, an MVP code, or "VERDICT" for NOT_EVALUABLE targets
    expected: Literal["INCONCLUSIVE", "OUT_OF_SCOPE", "SUSPECTED", "NOT_EVALUABLE"]


class LabelerRecord(Contract):
    labeler_id: NonEmptyStr  # pseudonymous
    role: Literal["labeler_x", "labeler_y", "adjudicator", "author"]
    labeled_at: AwareDatetime
    saw_evaluator_outputs: bool


class GoldProvenance(Contract):
    labeling_protocol_version: NonEmptyStr
    case_card_sha256: Sha256Hex | None = None
    created_at: AwareDatetime
    derived_from_evaluator_output: Literal[False] = False
    single_labeler: bool = False  # B-10 fallback must be disclosed
    notes: str | None = None


class Adjudication(Contract):
    adjudicator_id: NonEmptyStr
    adjudicated_at: AwareDatetime
    contested_items: list[str] = Field(default_factory=list)
    notes: str | None = None


class GoldLabel(Contract):
    schema_version: Literal["gold_label/2.1.0"] = GOLD_LABEL_SCHEMA
    item_id: ItemId
    split: Split
    gates: dict[GateId, GoldGate]
    findings: list[GoldFinding] = Field(default_factory=list)
    inconclusive_checks: list[str] = Field(default_factory=list)
    na_checks: list[str] = Field(default_factory=list)
    oos_checks: list[str] = Field(default_factory=list)
    verdict: GoldVerdict
    outcome: GoldOutcome = Field(default_factory=GoldOutcome)
    tags: GoldTags = Field(default_factory=GoldTags)
    abstention_targets: list[AbstentionTarget] = Field(default_factory=list)
    provenance: GoldProvenance
    labelers: list[LabelerRecord] = Field(min_length=1)
    adjudication: Adjudication | None = None

    @model_validator(mode="after")
    def _invariants(self) -> "GoldLabel":
        errs: list[str] = []
        missing = [g.value for g in GATE_IDS if g not in self.gates]
        if missing:
            errs.append(f"gold must state every gate explicitly (SD-01); missing {missing}")
        lists = {"inconclusive_checks": self.inconclusive_checks, "na_checks": self.na_checks,
                 "oos_checks": self.oos_checks}
        seen: dict[str, str] = {}
        for name, codes in lists.items():
            for c in codes:
                if c in {g.value for g in GATE_IDS}:
                    errs.append(f"{name} must not contain gates (gates carry explicit status): {c}")
                if c in seen:
                    errs.append(f"{c} appears in both {seen[c]} and {name}")
                seen[c] = name
        non_gate_findings = [f.code for f in self.findings if f.code not in {g.value for g in GATE_IDS}]
        for c in non_gate_findings:
            if c in seen:
                errs.append(f"{c} is both a finding and in {seen[c]}")
        if len(non_gate_findings) != len(set(non_gate_findings)):
            errs.append("duplicate non-gate finding codes (fold repeated instances into one finding)")
        for f in self.findings:
            if f.repair_status is RepairStatus.REPAIRED and f.code not in REPAIRABLE_CODES:
                errs.append(f"{f.code}: only {sorted(REPAIRABLE_CODES)} can be repaired (AJ-08)")
            if f.code in {g.value for g in GATE_IDS}:
                gg = self.gates.get(GateId(f.code))
                if gg is not None and gg.status not in (GateStatus.FAIL, GateStatus.INCONCLUSIVE):
                    errs.append(f"gate finding {f.code} requires gold gate status FAIL or INCONCLUSIVE")
        if any(g.status is GateStatus.FAIL for g in self.gates.values()) and \
                self.verdict.value is not Verdict.CRITICAL_FAIL:
            errs.append("a gold gate FAIL requires verdict CRITICAL_FAIL (V3)")
        if self.split in (Split.HOLDOUT, Split.REDTEAM) and any(lb.saw_evaluator_outputs for lb in self.labelers):
            errs.append("holdout / red-team gold must be labeled blind to evaluator outputs")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    def gate(self, g: str) -> GoldGate:
        return self.gates[GateId(g)]
