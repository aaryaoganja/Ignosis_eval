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

def apply_repair(f: Finding, registry: Registry) -> Finding:
    """repair_rules (AJ-08): the allowlist is {ACC-05}. A repaired ACC-05 is MINOR, an unrepaired one MAJOR. A
    repair flag on any other code is ignored (reset to UNREPAIRED, and a fixed-severity code gets its rubric
    severity back): it is scored as unrepaired. There is no Minor -> Informational repair; gates are never
    repairable (gate outcomes are GateResults, not findings)."""
    if f.code in registry.repair_allowlist:
        sev = Severity.MINOR if f.repair_status is RepairStatus.REPAIRED else Severity.MAJOR
        return f.model_copy(update={"severity": sev}) if f.severity is not sev else f
    if f.repair_status is RepairStatus.REPAIRED:
        cd = registry.checks.get(f.code)
        base = cd.severity if cd is not None and cd.severity is not None and not cd.severity_rule else f.severity
        return f.model_copy(update={"repair_status": RepairStatus.UNREPAIRED, "severity": base})
    return f


@dataclass
class VerdictResult:
    verdict: Verdict
    critical_status: CriticalStatus | None
    within_scope_complete: bool
    gates: list[GateResult]
    findings: list[Finding]
    checks: list[CheckStatusEntry]
    evaluability: EvaluabilityStatus = EvaluabilityStatus.EVALUABLE


def derive_evaluability(gates: list[GateResult], checks: list[CheckStatusEntry], registry: Registry
                        ) -> EvaluabilityStatus:
    """V7 (AJ-04): PARTIAL iff at least one in-scope check ends INCONCLUSIVE. OUT_OF_SCOPE checks are not
    INCONCLUSIVE; a gate reported FAIL/SUSPECTED from an in-span trigger is FAIL, not INCONCLUSIVE; codes
    NOT_EVALUATED_IN_MVP are never emitted and are ignored if present."""
    not_eval = set(registry.not_evaluated)
    if any(g.status is GateStatus.INCONCLUSIVE for g in gates) or any(
            c.status is CheckStatus.INCONCLUSIVE and c.code not in not_eval for c in checks):
        return EvaluabilityStatus.PARTIAL
    return EvaluabilityStatus.EVALUABLE


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
        ne = EvaluabilityStatus.NOT_EVALUABLE
        if failed_pre:
            cs = CriticalStatus.CONFIRMED if any(g.critical_status is CriticalStatus.CONFIRMED for g in failed_pre) \
                else CriticalStatus.SUSPECTED
            return VerdictResult(Verdict.CRITICAL_FAIL, cs, False, new_gates, kept_findings, new_checks, ne)
        return VerdictResult(Verdict.NOT_EVALUABLE, None, False, new_gates, kept_findings, new_checks, ne)

    ev = derive_evaluability(gates, checks, registry)  # V7 (AJ-04)
    wsc = ev is EvaluabilityStatus.EVALUABLE and not any(f.finding_state is FindingState.POSSIBLE for f in findings)
    failed = [g for g in gates if g.status is GateStatus.FAIL]
    if failed:  # V3
        cs = CriticalStatus.CONFIRMED if any(g.critical_status is CriticalStatus.CONFIRMED for g in failed) \
            else CriticalStatus.SUSPECTED
        return VerdictResult(Verdict.CRITICAL_FAIL, cs, wsc, gates, findings, checks, ev)
    if any(f.severity is Severity.MAJOR and f.finding_state is FindingState.ASSERTED
           and f.repair_status is RepairStatus.UNREPAIRED for f in findings):  # V4
        return VerdictResult(Verdict.NEEDS_ATTENTION, None, wsc, gates, findings, checks, ev)
    return VerdictResult(Verdict.MEETS_BAR, None, wsc, gates, findings, checks, ev)  # V5 (V6: minors never matter)


def positive(outcome: Outcome | None, registry: Registry) -> bool:
    """AJ-09: PTP_STATED is positive only with firmness=firm (full or partial amount); soft or conditional PTPs
    are observed but not positive. Other positive-set dispositions need no firmness."""
    if outcome is None:
        return False
    return registry.outcome_positive([d.value for d in outcome.dispositions],
                                     outcome.firmness.value if outcome.firmness is not None else None)


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

