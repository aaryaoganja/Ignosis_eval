"""Shared deterministic engine (frozen-contract §6, §7, §9, §10): confidence ceiling, attribution, verifier,
verdict V2–V7, repair, tags, routing. Driven by hand-written synthetic fixtures only."""

from __future__ import annotations

import pytest

import factories as F
from ignosis_eval.contracts.enums import (
    Confidence,
    Disposition,
    EvaluabilityStatus,
    FindingState,
    Firmness,
    GateStatus,
    InputMode,
    OutcomeAttribution,
    ReasonCode,
    RepairStatus,
    Severity,
    TranscriptProvenance,
)
from ignosis_eval.contracts.evaluation_record import Outcome, RecordBody, SystemInfo
from ignosis_eval.engine import confidence as conf
from ignosis_eval.engine.attribution import attribute
from ignosis_eval.engine.finalize import DerivationLog, Facts, finalize
from ignosis_eval.engine.verdict import apply_repair, decide, routing_tier, tags


def _ni(spec, turns=F.STUB_TURNS, evaluability="EVALUABLE", reasons=()):
    return F.make_ni(spec, turns, evaluability=evaluability, reasons=tuple(reasons))


# ------------------------------------------------------------------ confidence (R-01/R-11/R-12)
def test_high_requires_quote_reliable_spans_and_basis(spec):
    g4 = spec.registry.get("G4")
    kw = dict(sub_rule=None, quote_ok=True, role_ok=True, spans_reliable=True, lexicon_hit_without_negation=False)
    assert conf.compute(g4, det_confirmed=True, **kw) is Confidence.HIGH
    assert conf.compute(g4, det_confirmed=False, **kw) is Confidence.MEDIUM
    assert conf.compute(g4, det_confirmed=True, **{**kw, "quote_ok": False}) is Confidence.MEDIUM
    assert conf.compute(g4, det_confirmed=True, **{**kw, "spans_reliable": False}) is Confidence.LOW


def test_llm_label_only_lowers(spec):
    g4 = spec.registry.get("G4")
    kw = dict(sub_rule=None, quote_ok=True, role_ok=True, spans_reliable=True, lexicon_hit_without_negation=False)
    assert conf.compute(g4, det_confirmed=False, llm_label=Confidence.HIGH, **kw) is Confidence.MEDIUM
    assert conf.compute(g4, det_confirmed=True, llm_label=Confidence.LOW, **kw) is Confidence.LOW


def test_lexicon_only_codes_and_ceilings(spec):
    g3 = spec.registry.get("G3")
    kw = dict(sub_rule=None, quote_ok=True, role_ok=True, spans_reliable=True)
    assert conf.compute(g3, det_confirmed=True, lexicon_hit_without_negation=False, **kw) is Confidence.MEDIUM
    assert conf.compute(g3, det_confirmed=False, lexicon_hit_without_negation=True, **kw) is Confidence.HIGH
    pol = spec.registry.get("POL-01")
    assert conf.ceiling(pol, "POL-01a") is Confidence.MEDIUM and conf.ceiling(pol, "POL-01b") is Confidence.HIGH
    assert conf.ceiling(pol, None) is Confidence.MEDIUM  # unknown sub-rule: most conservative


def test_gate_routing():
    assert conf.route_gate(GateStatus.FAIL, Confidence.HIGH, False)[1].value == "CONFIRMED"
    assert conf.route_gate(GateStatus.FAIL, Confidence.MEDIUM, False)[1].value == "SUSPECTED"
    assert conf.route_gate(GateStatus.INCONCLUSIVE, Confidence.MEDIUM, True) == (GateStatus.FAIL,
                                                                                conf.CriticalStatus.SUSPECTED)
    assert conf.route_gate(GateStatus.INCONCLUSIVE, None, False) == (GateStatus.INCONCLUSIVE, None)
    assert conf.finding_state(Severity.MAJOR, Confidence.LOW).value == "POSSIBLE"


# ------------------------------------------------------------------ attribution (§7)
def test_attribution_rules(spec):
    reg = spec.registry
    t, at = InputMode.TRANSCRIPT, InputMode.AUDIO_TRANSCRIPT
    plat = TranscriptProvenance.PLATFORM_LIVE_ASR
    assert attribute(reg.get("G3"), input_mode=t, provenance=None).primary.value == "AGENT_BEHAVIOR"
    assert attribute(reg.get("UND-01"), input_mode=t, provenance=None, registered=True).primary.value == "AGENT_BEHAVIOR"
    r = attribute(reg.get("UND-01"), input_mode=t, provenance=None, own_span_confidence_low=True)
    assert r.primary.value == "INDETERMINATE" and "perception_plausible" in r.notes
    r = attribute(reg.get("UND-03"), input_mode=at, provenance=plat, material_difference=True,
                  readback_safeguard=False)
    assert (r.primary.value, r.secondary.value, r.basis.value) == ("PERCEPTION", "AGENT_BEHAVIOR",
                                                                  "DECLARED_PROVENANCE")
    # content_perception has no registration override; outside A+T + platform_live_asr -> INDETERMINATE
    assert attribute(reg.get("UND-03"), input_mode=at, provenance=TranscriptProvenance.HUMAN, registered=True,
                     material_difference=True).primary.value == "INDETERMINATE"
    assert attribute(reg.get("PLT-01"), input_mode=t, provenance=None).primary.value == "PLATFORM_AUDIO"
    assert attribute(reg.get("PLT-04"), input_mode=at, provenance=plat).primary.value == "PERCEPTION"
    assert attribute(reg.get("G7"), input_mode=t, provenance=None).primary.value == "INDETERMINATE"
    assert attribute(reg.get("G1"), input_mode=t, provenance=None).basis.value == "PROFILE_DEFAULT"


# ------------------------------------------------------------------ verdict V2–V7, repair, tags, routing
def test_verdict_rules(spec):
    reg = spec.registry
    gates = [F.gate(g) for g in F.GATES]
    maj = F.finding("UND-01", [2], severity="MAJOR")
    minor = F.finding("UND-12", [2], severity="MINOR")
    ok = dict(evaluability=EvaluabilityStatus.EVALUABLE, reason_codes=[], checks=[], registry=reg)
    assert decide(gates=gates, findings=[minor], **ok).verdict.value == "MEETS_BAR"  # V6
    assert decide(gates=gates, findings=[maj], **ok).verdict.value == "NEEDS_ATTENTION"  # V4
    rep = maj.model_copy(update={"repair_status": RepairStatus.REPAIRED})
    assert apply_repair(rep, reg).severity is Severity.MINOR
    assert decide(gates=gates, findings=[apply_repair(rep, reg)], **ok).verdict.value == "MEETS_BAR"
    possible = maj.model_copy(update={"finding_state": FindingState.POSSIBLE})
    r = decide(gates=gates, findings=[possible], **ok)
    assert r.verdict.value == "MEETS_BAR" and r.within_scope_complete is False  # V7
    failed = [F.gate("G3", "FAIL", critical_status="CONFIRMED") if g.gate.value == "G3" else g for g in gates]
    r = decide(gates=failed, findings=[maj], **ok)
    assert (r.verdict.value, r.critical_status.value) == ("CRITICAL_FAIL", "CONFIRMED")  # V3


def test_v2_not_evaluable(spec):
    reg = spec.registry
    gates = [F.gate(g) for g in F.GATES]
    r = decide(evaluability=EvaluabilityStatus.NOT_EVALUABLE, reason_codes=[ReasonCode.ROLE_UNCERTAIN], gates=gates,
               findings=[F.finding("UND-01", [2])], checks=[], registry=reg)
    assert r.verdict.value == "NOT_EVALUABLE" and r.within_scope_complete is False
    st = {g.gate.value: g.status.value for g in r.gates}
    assert st["G3"] == "INCONCLUSIVE" and "PASS" not in {st[g] for g in ("G1", "G2", "G3", "G4", "G5", "G6")}
    pre_fail = [F.gate("G7", "FAIL", critical_status="CONFIRMED") if g.gate.value == "G7" else g for g in gates]
    r = decide(evaluability=EvaluabilityStatus.NOT_EVALUABLE, reason_codes=[ReasonCode.ROLE_UNCERTAIN],
               gates=pre_fail, findings=[], checks=[], registry=reg)
    assert r.verdict.value == "CRITICAL_FAIL"  # pre-check gate failed


def test_tags_and_routing(spec):
    reg = spec.registry
    gates = [F.gate(g) for g in F.GATES]
    out = Outcome(dispositions=[Disposition.PTP_STATED], firmness=Firmness.FIRM, commitment_turn=5)
    ind = F.finding("TRT-01", [3], anchor_turn=3)
    t = tags(verdict=decide(evaluability=EvaluabilityStatus.EVALUABLE, reason_codes=[], gates=gates, findings=[ind],
                            checks=[], registry=reg).verdict, gates=gates, findings=[ind], outcome=out, registry=reg)
    assert t.dangerous_win.value == "MATERIAL" and t.high_friction is None and t.coverage == "conversation_only"
    fail = [F.gate("G4", "FAIL") if g.gate.value == "G4" else g for g in gates]
    assert tags(verdict=decide(evaluability=EvaluabilityStatus.EVALUABLE, reason_codes=[], gates=fail, findings=[],
                               checks=[], registry=reg).verdict, gates=fail, findings=[], outcome=out,
                registry=reg).dangerous_win.value == "CRITICAL"
    lost = Outcome(dispositions=[Disposition.REFUSED], outcome_attribution=OutcomeAttribution.CUSTOMER_DRIVEN)
    from ignosis_eval.contracts.enums import Verdict

    assert tags(verdict=Verdict.MEETS_BAR, gates=gates, findings=[], outcome=lost, registry=reg).clean_loss
    assert routing_tier(verdict=Verdict.CRITICAL_FAIL, critical_status=conf.CriticalStatus.SUSPECTED, tags_=t,
                        findings=[]) == 2
    assert routing_tier(verdict=Verdict.NEEDS_ATTENTION, critical_status=None, tags_=t, findings=[ind]) == 3


# ------------------------------------------------------------------ finalize (verifier + capability + pre-checks)
def test_finalize_verifier_and_capability(spec):
    ni = _ni(spec)
    body = RecordBody(gates=[F.gate("G3", "FAIL", evidence=[F.ev(3, "fabricated words never said")]),
                             F.gate("G7", "PASS")],
                      findings=[F.finding("UND-01", [2], quote="also not in the transcript", role="BORROWER"),
                                F.finding("PLT-02", [1], severity="MINOR"),
                                F.finding("ACC-01", [1])])
    log = DerivationLog()
    rec = finalize(body, ni, spec, system=SystemInfo(system="A+", version="t"), facts=Facts(), log=log)
    g = {x.gate.value: x for x in rec.gates}
    assert g["G3"].status.value == "FAIL" and g["G3"].critical_status.value == "SUSPECTED"
    assert g["G3"].evidence_unverified  # §9.6
    assert g["G7"].status.value == "OUT_OF_SCOPE"  # pre-check authoritative (no header)
    assert g["G1"].status.value == "INCONCLUSIVE"  # missing gate never becomes PASS
    assert [f.code for f in rec.findings] == []  # unverifiable non-gate dropped; PLT-02 OOS in T; ACC-01 always OOS
    assert {c.code: c.status.value for c in rec.checks}["PLT-02"] == "OUT_OF_SCOPE"
    assert {o.code for o in rec.out_of_scope} >= {"ACC-01", "EXE-01", "OUTCOME_VERIFIED"}
    assert rec.verdict.value == "CRITICAL_FAIL" and rec.critical_status.value == "SUSPECTED"
    assert any(e["step"] == "verifier" for e in log.entries)


def test_finalize_verified_gate_keeps_computed_confidence(spec):
    ni = _ni(spec)
    body = RecordBody(gates=[F.gate("G4", "FAIL", critical_status="CONFIRMED", confidence="HIGH",
                                    evidence=[F.ev(3, "stub agent line gamma")])])
    rec = finalize(body, ni, spec, system=SystemInfo(system="A+", version="t"))
    g4 = rec.gate("G4")
    assert g4.confidence.value == "MEDIUM" and g4.critical_status.value == "SUSPECTED"  # no det confirmation (A+)
    rec = finalize(body, ni, spec, system=SystemInfo(system="B", version="t"), facts=Facts(det_confirmed={"G4": True}))
    assert rec.gate("G4").critical_status.value == "CONFIRMED"


def test_finalize_not_evaluable_front_end_wins(spec):
    ni = _ni(spec, evaluability="NOT_EVALUABLE", reasons=[ReasonCode.ROLE_UNCERTAIN])
    rec = finalize(RecordBody(gates=[F.gate(g) for g in F.GATES], verdict="MEETS_BAR"), ni, spec,
                   system=SystemInfo(system="A+", version="t"))
    assert rec.verdict.value == "NOT_EVALUABLE" and rec.within_scope_complete is False


@pytest.mark.parametrize("code", ["UND-11", "COM-04", "PLT-05"])
def test_not_evaluated_codes_are_schema_errors(spec, code):
    from ignosis_eval.contracts.record_checks import schema_errors

    rec = F.record(findings=[F.finding(code, [1])])
    assert schema_errors(rec, spec.registry)
