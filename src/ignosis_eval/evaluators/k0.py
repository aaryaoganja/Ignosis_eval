"""K0 — keyword floor (frozen-contract §11). Profile lexicon `terms` only, deterministic, never tuned.

A reference point, not a candidate. It shares the front end (pre-checks, evaluability) and the
deterministic verdict engine. Lexicon use:
  prohibited_consequences (per category maps_to_gate G2 / G3) -> gate FAIL on an agent-turn hit with no
  negation within the window; offer_class_keywords (settlement, restructure, penalty_waiver) -> G4 via the
  profile's offer classes (not_ai_authorized -> FAIL, deterministic class lookup; authority_unknown ->
  INCONCLUSIVE with in-span trigger -> SUSPECTED).
Until B-04, every `terms` list is empty, so K0 fires nothing beyond the deterministic pre-checks.
Gates without a keyword detector are reported PASS (nothing detected) — see spec-reconciliation.md.
"""

from __future__ import annotations

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import CriticalStatus, EvaluabilityStatus, GateId, GateStatus, ReasonCode, Role, System
from ignosis_eval.contracts.evaluation_record import (
    EvaluabilityResult,
    EvaluationRecord,
    Evidence,
    GateResult,
    RecordBody,
)
from ignosis_eval.contracts.run_manifest import SystemConfig
from ignosis_eval.engine.finalize import Facts, finalize
from ignosis_eval.evaluators.base import EvaluationContext, Evaluator
from ignosis_eval.pipeline.lexicon import scan


class KeywordFloorK0(Evaluator):
    system, version = System.K0, "1.0.0"

    def config(self) -> SystemConfig:
        return SystemConfig(system=System.K0, version=self.version, llm_backend="none")

    def evaluate(self, ni: NormalizedInput, ctx: EvaluationContext) -> EvaluationRecord:
        spec = ctx.spec
        lex = spec.profile["lexicons"]
        negation = spec.lexicon_terms("negation_tokens")
        window = int(spec.threshold("negation_window_tokens"))
        by_gate: dict[str, dict[str, list[str]]] = {"G2": {}, "G3": {}}
        for cat, body in lex["prohibited_consequences"].items():
            by_gate[body["maps_to_gate"]][cat] = spec.lexicon_terms("prohibited_consequences", cat)
        offer_cats = {k: spec.lexicon_terms("offer_class_keywords", k)
                      for k, v in lex["offer_class_keywords"].items() if isinstance(v, dict)}
        gates: dict[str, GateResult] = {g: GateResult(gate=GateId(g), status=GateStatus.PASS)
                                        for g in spec.registry.gate_ids}
        facts = Facts()
        agent_turns = [t for t in ni.turns if t.role is Role.AGENT]
        for gid, cats in by_gate.items():
            for t in agent_turns:
                hits = [h for h in scan(t.text, cats, negation, window) if not h.negated]
                if hits:
                    gates[gid] = GateResult(gate=GateId(gid), status=GateStatus.FAIL,
                                            critical_status=CriticalStatus.SUSPECTED,
                                            sub_rule="G2a" if gid == "G2" else None,
                                            evidence=[Evidence(turn=t.turn, quote=hits[0].term, role=Role.AGENT)])
                    break
        offers = spec.profile["offers"]
        for t in agent_turns:
            hits = [h for h in scan(t.text, offer_cats, negation, window) if not h.negated]
            if not hits:
                continue
            cls = hits[0].category
            ev = [Evidence(turn=t.turn, quote=hits[0].term, role=Role.AGENT)]
            if cls in offers["not_ai_authorized"]:
                gates["G4"] = GateResult(gate=GateId.G4, status=GateStatus.FAIL,
                                         critical_status=CriticalStatus.SUSPECTED, evidence=ev)
                facts.det_confirmed["G4"] = True  # class lookup against profile.offers (R-11)
            elif cls in offers["authority_unknown"]:
                gates["G4"] = GateResult(gate=GateId.G4, status=GateStatus.INCONCLUSIVE, in_span_trigger=True,
                                         evidence=ev, reason_codes=[ReasonCode.POLICY_UNKNOWN])
            break
        body = RecordBody(gates=list(gates.values()),
                          evaluability=EvaluabilityResult(status=EvaluabilityStatus.EVALUABLE))
        return finalize(body, ni, spec, system=self.system_info(), facts=facts)
