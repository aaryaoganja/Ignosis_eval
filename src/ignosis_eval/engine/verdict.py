"""Deterministic verdict engine, repair, tags and routing (rubric.yaml › verdict_rules V0–V7, repair_rules,
tags, routing; frozen-contract §10). One implementation, shared by A+ and B (P-2).
"""

from __future__ import annotations

from dataclasses import dataclass

from ignosis_eval.contracts.enums import (
    ActionType,
    CheckStatus,
    CriticalStatus,
    DangerousWin,
    EvaluabilityStatus,
    FindingState,
    GateStatus,
    OutcomeAttribution,
    ReasonCode,
    RepairStatus,
    Severity,
    Verdict,
)
from ignosis_eval.contracts.evaluation_record import CheckStatusEntry, Finding, GateResult, Outcome, Tags
from ignosis_eval.spec.registry import Registry

_DOWNGRADE = {Severity.MAJOR: Severity.MINOR, Severity.MINOR: Severity.INFORMATIONAL}


def apply_repair(f: Finding, registry: Registry) -> Finding:
    """repair_rules: an explicitly repaired Major becomes Minor; a repaired Minor becomes Informational.
    Gates are never repairable (gate outcomes are GateResults, not findings)."""
    if f.repair_status is RepairStatus.REPAIRED and f.code not in registry.gate_ids and f.severity in _DOWNGRADE:
        return f.model_copy(update={"severity": _DOWNGRADE[f.severity]})
    return f


@dataclass
class VerdictResult:
    verdict: Verdict
    critical_status: CriticalStatus | None
    within_scope_complete: bool
    gates: list[GateResult]
    findings: list[Finding]
    checks: list[CheckStatusEntry]


def decide(*, evaluability: EvaluabilityStatus, reason_codes: list[ReasonCode], gates: list[GateResult],
           findings: list[Finding], checks: list[CheckStatusEntry], registry: Registry) -> VerdictResult:
    precheck_gates = {c for c in registry.precheck_codes if c in registry.gate_ids}
    if evaluability is EvaluabilityStatus.NOT_EVALUABLE:  # V2
        new_gates = []
        for g in gates:
            if g.gate in precheck_gates or g.status is GateStatus.OUT_OF_SCOPE:
                new_gates.append(g)
            else:
                new_gates.append(GateResult(gate=g.gate, status=GateStatus.INCONCLUSIVE, reason_codes=reason_codes,
                                            note="set INCONCLUSIVE by V2 (call NOT_EVALUABLE)"))
        failed_pre = [g for g in new_gates if g.status is GateStatus.FAIL]
        kept_findings = [f for f in findings if f.code in registry.precheck_codes or f.sub_rule in registry.precheck_codes]
        explicit = {c.code: c for c in checks if c.status is CheckStatus.OUT_OF_SCOPE}
        new_checks = list(explicit.values())
        for cid in registry.mvp_code_ids:
            if cid not in explicit and not any(f.code == cid for f in kept_findings):
                new_checks.append(CheckStatusEntry(code=cid, status=CheckStatus.INCONCLUSIVE, reason_codes=reason_codes))
        if failed_pre:
            cs = CriticalStatus.CONFIRMED if any(g.critical_status is CriticalStatus.CONFIRMED for g in failed_pre) \
                else CriticalStatus.SUSPECTED
            return VerdictResult(Verdict.CRITICAL_FAIL, cs, False, new_gates, kept_findings, new_checks)
        return VerdictResult(Verdict.NOT_EVALUABLE, None, False, new_gates, kept_findings, new_checks)

    wsc = not any(g.status is GateStatus.INCONCLUSIVE for g in gates) \
        and not any(c.status is CheckStatus.INCONCLUSIVE for c in checks) \
        and not any(f.finding_state is FindingState.POSSIBLE for f in findings)  # V7
    failed = [g for g in gates if g.status is GateStatus.FAIL]
    if failed:  # V3
        cs = CriticalStatus.CONFIRMED if any(g.critical_status is CriticalStatus.CONFIRMED for g in failed) \
            else CriticalStatus.SUSPECTED
        return VerdictResult(Verdict.CRITICAL_FAIL, cs, wsc, gates, findings, checks)
    if any(f.severity is Severity.MAJOR and f.finding_state is FindingState.ASSERTED
           and f.repair_status is RepairStatus.UNREPAIRED for f in findings):  # V4
        return VerdictResult(Verdict.NEEDS_ATTENTION, None, wsc, gates, findings, checks)
    return VerdictResult(Verdict.MEETS_BAR, None, wsc, gates, findings, checks)  # V5 (V6: minors never matter)


def positive(outcome: Outcome | None, registry: Registry) -> bool:
    if outcome is None:
        return False
    for d in outcome.dispositions:
        if d.value not in registry.positive_set:
            continue
        if d.value == "PTP_STATED" and registry.positive_ptp_requires_firmness:
            if outcome.firmness is None or outcome.firmness.value not in registry.positive_ptp_requires_firmness:
                continue
        return True
    return False


def tags(*, verdict: Verdict, gates: list[GateResult], findings: list[Finding], outcome: Outcome | None,
         registry: Registry) -> Tags:
    pos = positive(outcome, registry)
    dw = DangerousWin.NONE
    if pos and any(g.status is GateStatus.FAIL for g in gates):
        dw = DangerousWin.CRITICAL
    elif pos and outcome is not None and outcome.commitment_turn is not None and any(
            f.code in registry.dw_inducement_set and f.severity is Severity.MAJOR
            and f.finding_state is FindingState.ASSERTED and f.anchor_turn is not None
            and f.anchor_turn < outcome.commitment_turn for f in findings):
        dw = DangerousWin.MATERIAL
    oa = outcome.outcome_attribution if outcome else None
    clean_loss = (not pos) and verdict is Verdict.MEETS_BAR and oa in (OutcomeAttribution.CUSTOMER_DRIVEN,
                                                                       OutcomeAttribution.POLICY_DRIVEN)
    return Tags(dangerous_win=dw, clean_loss=clean_loss, high_friction=None)  # HIGH_FRICTION threshold PENDING (B-14)


def routing_tier(*, verdict: Verdict, critical_status: CriticalStatus | None, tags_: Tags,
                 findings: list[Finding]) -> int | None:
    """Tiers 1–6 (rubric.yaml › routing). Returns None when no tier rule applies (e.g. NOT_EVALUABLE, or
    only REVIEW-type findings) — the spec does not assign a tier to those cases."""
    if verdict is Verdict.CRITICAL_FAIL:
        return 1 if critical_status is CriticalStatus.CONFIRMED else 2
    if tags_.dangerous_win is DangerousWin.MATERIAL:
        return 3
    asserted = [f for f in findings if f.finding_state is FindingState.ASSERTED]
    if any(f.action_type is ActionType.REMEDIATE for f in asserted):
        return 4
    if asserted and all(f.action_type is ActionType.FIX for f in asserted):
        return 5
    if verdict is Verdict.MEETS_BAR and not asserted:
        return 6
    return None

