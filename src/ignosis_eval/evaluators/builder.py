"""RecordBuilder: assembles an Evaluation Record and applies the shared aggregation rules.

Aggregation (PROVISIONAL — evaluator-side policy, see docs/gap-analysis.md G4/G13):
  1. OUT_OF_SCOPE evaluability  -> verdict out_of_scope (gates/findings dropped).
  2. Gate precedence: any gate FAIL, or any unrepaired CRITICAL finding attributed to the agent (or
     undetermined)  -> verdict FAIL, evaluability evaluable.
  3. Any unrepaired MAJOR finding attributed to the agent (or undetermined) -> FAIL.
  4. Else, evaluability INCONCLUSIVE requested, or any gate INCONCLUSIVE -> verdict inconclusive.
  5. Else PASS.
Routing: FAIL with a critical basis -> compliance_escalation; other FAIL / inconclusive / low confidence
-> human_review; out_of_scope -> no_action; else auto_accept.
Dangerous win = outcome class WIN and a verdict-driving critical/major finding; clean loss = outcome
class LOSS and no critical/major agent-attributed finding. Both null when the verdict is an abstention.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.enums import (
    AttributionTarget,
    EvaluabilityStatus,
    EvidenceModality,
    GateStatus,
    OutcomeClass,
    OutcomeCode,
    RepairStatus,
    RoutingDecision,
    Severity,
    Speaker,
    Verdict,
)
from ignosis_eval.contracts.evaluation_record import (
    AttributionClaim,
    ConfidenceSummary,
    DimensionResult,
    EvaluabilityAssessment,
    EvaluationRecord,
    EvaluatorInfo,
    EvidenceItem,
    Finding,
    GateResult,
    ObservableOutcomes,
    Routing,
)
from ignosis_eval.contracts.profile import Profile

AGENT_SIDE = {AttributionTarget.AGENT_LOGIC, AttributionTarget.UNDETERMINED}
LOW_CONFIDENCE = 0.7


class RecordBuilder:
    def __init__(self, info: EvaluatorInfo, inp: CanonicalInput, profile: Profile, profile_sha256: str,
                 rep_seed: int):
        self.info, self.inp, self.profile, self.profile_sha256 = info, inp, profile, profile_sha256
        self.rep_seed = rep_seed
        self.evidence: list[EvidenceItem] = []
        self._ev_index: dict[tuple, str] = {}
        self.gates: dict[str, GateResult] = {}
        self.findings: list[Finding] = []
        self.dimensions: list[DimensionResult] = []
        self.outcomes: list[OutcomeCode] = []
        self.outcome_evidence: list[str] = []
        self.primary_attribution: AttributionClaim | None = None
        self.evaluability = EvaluabilityStatus.EVALUABLE
        self.evaluability_reasons: list[str] = []
        self.warnings: list[str] = []

    # ------------------------------------------------------------------------------------ evidence
    def add_evidence(self, modality: EvidenceModality | str, turn_ids: list[str] | None = None,
                     quote: str | None = None, *, start_ms: int | None = None, end_ms: int | None = None,
                     speaker: Speaker | str | None = None, metadata_field: str | None = None) -> str:
        key = (str(modality), tuple(turn_ids or ()), quote, start_ms, end_ms, str(speaker), metadata_field)
        if key in self._ev_index:
            return self._ev_index[key]
        eid = f"e{len(self.evidence) + 1}"
        self.evidence.append(EvidenceItem(
            evidence_id=eid, modality=EvidenceModality(modality), turn_ids=list(turn_ids or []), quote=quote,
            start_ms=start_ms, end_ms=end_ms, speaker=Speaker(speaker) if speaker else None,
            metadata_field=metadata_field))
        self._ev_index[key] = eid
        return eid

    def add_evidence_dicts(self, items: list[dict[str, Any]]) -> list[str]:
        ids = []
        for e in items or []:
            ids.append(self.add_evidence(e.get("modality", "transcript"), e.get("turn_ids"), e.get("quote"),
                                         start_ms=e.get("start_ms"), end_ms=e.get("end_ms"),
                                         speaker=e.get("speaker"), metadata_field=e.get("metadata_field")))
        return ids

    # ------------------------------------------------------------------------------------ content
    def set_gate(self, gate_id: str, status: GateStatus | str, evidence_ids: list[str] | None = None,
                 confidence: float | None = None, rationale: str | None = None) -> None:
        self.gates[gate_id] = GateResult(gate_id=gate_id, status=GateStatus(status), evidence_ids=evidence_ids or [],
                                         confidence=confidence, rationale=rationale)

    def add_finding(self, defect_id: str, evidence_ids: list[str], attribution: AttributionTarget | str,
                    confidence: float | None = None, repair_status: RepairStatus | str = RepairStatus.NOT_REPAIRED,
                    description: str | None = None) -> None:
        dd = self.profile.defect_def(defect_id)
        if dd is None:
            self.warnings.append(f"dropped finding with defect id unknown to profile: {defect_id}")
            return
        self.findings.append(Finding(
            finding_id=f"f{len(self.findings) + 1}", defect_id=defect_id, severity=dd.severity, gate_id=dd.gate_id,
            dimension_id=dd.dimension_id, repair_status=RepairStatus(repair_status), evidence_ids=evidence_ids,
            attribution=AttributionClaim(target=AttributionTarget(attribution), evidence_ids=[]),
            confidence=confidence, description=description))

    def set_dimension(self, dimension_id: str, score: float | None, evidence_ids: list[str] | None = None) -> None:
        dd = self.profile.dimension_def(dimension_id)
        if dd is None:
            return
        self.dimensions.append(DimensionResult(
            dimension_id=dimension_id, evaluable=score is not None, score=score, scale_min=dd.scale_min,
            scale_max=dd.scale_max, evidence_ids=evidence_ids or []))

    def set_outcome(self, outcomes: list[OutcomeCode | str], evidence_ids: list[str] | None = None) -> None:
        self.outcomes = [OutcomeCode(o) for o in outcomes]
        self.outcome_evidence = evidence_ids or []

    def set_evaluability(self, status: EvaluabilityStatus | str, reasons: list[str] | None = None) -> None:
        self.evaluability = EvaluabilityStatus(status)
        self.evaluability_reasons = list(reasons or [])

    # ------------------------------------------------------------------------------------ aggregation
    def _outcome_class(self) -> OutcomeClass:
        classes = {self.profile.outcome_classes[o] for o in self.outcomes}
        for c in (OutcomeClass.WIN, OutcomeClass.LOSS, OutcomeClass.NEUTRAL):
            if c in classes:
                return c
        return OutcomeClass.UNKNOWN

    def _driving(self, severities: set[Severity]) -> list[Finding]:
        return [f for f in self.findings if f.severity in severities and f.repair_status is not RepairStatus.REPAIRED
                and f.attribution.target in AGENT_SIDE]

    def build(self, confidence: float | None = None, confidence_method: str = "heuristic") -> EvaluationRecord:
        gates = list(self.gates.values())
        findings = list(self.findings)
        crit, major = self._driving({Severity.CRITICAL}), self._driving({Severity.MAJOR})
        failed_gates = [g for g in gates if g.status is GateStatus.FAIL]
        routing_reasons: list[str] = []
        evaluability = self.evaluability
        if evaluability is EvaluabilityStatus.OUT_OF_SCOPE:
            verdict, gates, findings = Verdict.OUT_OF_SCOPE, [], []
        elif failed_gates or crit:
            verdict, evaluability = Verdict.FAIL, EvaluabilityStatus.EVALUABLE
            routing_reasons.append("critical gate/finding")
        elif major:
            verdict, evaluability = Verdict.FAIL, EvaluabilityStatus.EVALUABLE
            routing_reasons.append("major finding")
        elif evaluability is EvaluabilityStatus.INCONCLUSIVE or any(g.status is GateStatus.INCONCLUSIVE for g in gates):
            verdict, evaluability = Verdict.INCONCLUSIVE, EvaluabilityStatus.INCONCLUSIVE
            if not self.evaluability_reasons:
                self.evaluability_reasons = [f"gate {g.gate_id} inconclusive" for g in gates
                                             if g.status is GateStatus.INCONCLUSIVE]
        else:
            verdict = Verdict.PASS

        if verdict is Verdict.OUT_OF_SCOPE:
            decision = RoutingDecision.NO_ACTION
        elif verdict is Verdict.FAIL and (failed_gates or crit):
            decision = RoutingDecision.COMPLIANCE_ESCALATION
        elif verdict in (Verdict.FAIL, Verdict.INCONCLUSIVE):
            decision = RoutingDecision.HUMAN_REVIEW
        elif confidence is not None and confidence < LOW_CONFIDENCE:
            decision, routing_reasons = RoutingDecision.HUMAN_REVIEW, ["low confidence"]
        else:
            decision = RoutingDecision.AUTO_ACCEPT

        outcome_class = self._outcome_class()
        if verdict in (Verdict.INCONCLUSIVE, Verdict.OUT_OF_SCOPE):
            dw = cl = None
        else:
            dw = outcome_class is OutcomeClass.WIN and bool(crit or major or failed_gates)
            cl = outcome_class is OutcomeClass.LOSS and not (crit or major or failed_gates)

        rid = hashlib.sha256(
            f"{self.info.name}|{self.info.version}|{self.inp.call_id}|{self.rep_seed}".encode()).hexdigest()[:24]
        return EvaluationRecord(
            record_id=f"rec-{rid}",
            call_id=self.inp.call_id,
            evaluator=self.info,
            rubric_version=self.profile.rubric_version,
            profile_id=self.profile.profile_id,
            profile_version=self.profile.profile_version,
            profile_sha256=self.profile_sha256,
            input_mode=self.inp.input_mode,
            evaluability=EvaluabilityAssessment(status=evaluability, reasons=self.evaluability_reasons),
            verdict=verdict,
            gates=gates,
            dimensions=self.dimensions if verdict is not Verdict.OUT_OF_SCOPE else [],
            findings=findings,
            evidence=self.evidence,
            confidence=ConfidenceSummary(overall=confidence, method=confidence_method if confidence is not None
                                         else "none"),
            primary_attribution=self.primary_attribution if verdict is not Verdict.OUT_OF_SCOPE else None,
            observable_outcomes=ObservableOutcomes(outcomes=self.outcomes, outcome_class=outcome_class,
                                                   evidence_ids=self.outcome_evidence),
            dangerous_win=dw,
            clean_loss=cl,
            routing=Routing(decision=decision, reasons=routing_reasons),
            warnings=self.warnings,
        )
