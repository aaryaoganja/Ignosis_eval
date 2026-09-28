"""Evaluator A's only post-processing besides schema validation (rubric.yaml › architecture_application.A, AJ-06):
merge the shared front-end results into A's record.

  * not-evaluable short-circuit: a NOT_EVALUABLE front end means no LLM call; the record is the deterministic V2
    record (pre-check gates kept, every other gate and code INCONCLUSIVE);
  * deterministic pre-checks: G7, the exact-string part of G8, G9 and POL-01b replace A's own values.

Nothing else is applied to A: no capability filter, no external-truth filter, no verifier, no confidence cap, no
attribution rules, no repair allowlist. A must be able to violate H1 and H6, or the baseline measures nothing.
Convention (docs/spec-reconciliation.md §3): a pre-check result can only raise A's verdict (a failed pre-check
gate -> CRITICAL_FAIL by V3; a POL-01b Major -> at least NEEDS_ATTENTION by V4); A's verdict is otherwise kept.
"""

from __future__ import annotations

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import (
    ConfidenceSource,
    CriticalStatus,
    EvaluabilityStatus,
    FindingState,
    GateStatus,
    RepairStatus,
    Severity,
    Verdict,
)
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, RecordBody, SystemInfo
from ignosis_eval.engine.finalize import finalize
from ignosis_eval.spec.loader import Spec

_RANK = {Verdict.MEETS_BAR: 1, Verdict.NEEDS_ATTENTION: 2, Verdict.CRITICAL_FAIL: 3}


def not_evaluable_short_circuit(ni: NormalizedInput) -> bool:
    return ni.frontend.evaluability_status is EvaluabilityStatus.NOT_EVALUABLE


def short_circuit_record(ni: NormalizedInput, spec: Spec, system: SystemInfo,
                         confidence_source: ConfidenceSource) -> EvaluationRecord:
    """The deterministic V2 record for a NOT_EVALUABLE front end (no LLM call)."""
    return finalize(RecordBody(), ni, spec, system=system, confidence_source=confidence_source)


def merge_frontend(record: EvaluationRecord, ni: NormalizedInput) -> EvaluationRecord:
    if record.record_status.value != "OK":
        return record
    gates = {g.gate.value: g for g in record.gates}
    for pre in ni.frontend.prechecks:
        gates[pre.gate.value] = pre
    findings = list(record.findings)
    for pf in ni.frontend.precheck_findings:
        if not any(f.code == pf.code and f.sub_rule == pf.sub_rule and f.anchor_turn == pf.anchor_turn
                   for f in findings):
            findings.append(pf)
    verdict, cs = record.verdict, record.critical_status
    failed_pre = [p for p in ni.frontend.prechecks if p.status is GateStatus.FAIL]
    if failed_pre and verdict is not Verdict.NOT_EVALUABLE:
        pre_cs = CriticalStatus.CONFIRMED if any(p.critical_status is CriticalStatus.CONFIRMED for p in failed_pre) \
            else CriticalStatus.SUSPECTED
        if verdict is not Verdict.CRITICAL_FAIL:
            verdict, cs = Verdict.CRITICAL_FAIL, pre_cs
        elif pre_cs is CriticalStatus.CONFIRMED:
            cs = CriticalStatus.CONFIRMED
    elif verdict in _RANK and _RANK[verdict] < _RANK[Verdict.NEEDS_ATTENTION] and any(
            f.severity is Severity.MAJOR and f.finding_state is FindingState.ASSERTED
            and f.repair_status is RepairStatus.UNREPAIRED for f in ni.frontend.precheck_findings):
        verdict = Verdict.NEEDS_ATTENTION
    data = record.model_dump(mode="json")
    data.update({"gates": [g.model_dump(mode="json") for g in gates.values()],
                 "findings": [f.model_dump(mode="json") for f in findings],
                 "verdict": verdict.value if verdict else None, "critical_status": cs.value if cs else None,
                 "confidence_source": ConfidenceSource.SELF_REPORTED.value})
    return EvaluationRecord.model_validate(data)
