"""Evaluator B's rule engine (engine/code_rules.py via SpecRuleEngine) on hand-written extraction fixtures.

Every transcript turn and quote is an obviously synthetic stub; nothing here is benchmark content.
"""

from __future__ import annotations

from typing import Any

import pytest

import factories as F
from ignosis_eval.contracts.evaluation_record import SystemInfo
from ignosis_eval.engine.code_rules import parse_values
from ignosis_eval.engine.finalize import finalize
from ignosis_eval.evaluators.judgement import ExtractionOutput, JudgmentOutput, SpecRuleEngine

SP = F.spec()
TURNS = (("AGENT", "stub agent opens zz org"), ("BORROWER", "stub borrower affirms"),
         ("AGENT", "stub agent states dues 5,000"), ("BORROWER", "stub borrower speaks"),
         ("AGENT", "stub agent asks date"), ("BORROWER", "stub borrower replies 12 tareekh 5,000"),
         ("AGENT", "stub agent reads back 22 tareekh 5,000"), ("BORROWER", "stub borrower confirms haan"),
         ("AGENT", "stub agent closes"))
NI = F.make_ni(SP, turns=TURNS)
LANGS = {str(i): "hi-en" for i in range(1, len(TURNS) + 1)}


def E(eid: int, etype: str, turn: int, **kw: Any) -> dict:
    return {"event_id": f"E{eid}", "type": etype, "turn": turn, "quote": kw.pop("quote", TURNS[turn - 1][1]),
            "source": "supplied", "confidence": "HIGH", **kw}


def ex(*events: dict, **kw: Any) -> ExtractionOutput:
    body = {"events": list(events), "turn_languages": kw.pop("turn_languages", LANGS),
            "call_frame": {"agent_org_statement": {"turn": 1, "quote": "zz org"},
                           "identity_checks": [{"turn": 2, "quote": "stub borrower affirms", "result": "affirmed"}],
                           "stage_hint": "overdue", **kw.pop("call_frame", {})},
            "call_end": kw.pop("call_end", {"ended_by": "agent", "pending_borrower_question_turn": None,
                                            "next_step_quote": "stub agent closes"}), **kw}
    return ExtractionOutput.model_validate(body)


def run(extraction: ExtractionOutput, answers: list[tuple[str, str, str]] | None = None, ni=NI):
    engine = SpecRuleEngine()
    res = engine.apply(extraction, ni, SP)
    requested = {(r.judgment, r.target) for r in res.judgments_needed}
    if res.judgments_needed:
        out = JudgmentOutput.model_validate({"answers": [
            {"judgment": j, "target": t, "answer": a, "cited_turns": [1]} for j, t, a in (answers or [])]})
        res = engine.integrate(res, out, SP)
    rec = finalize(res.body, ni, SP, system=SystemInfo(system="B", version="t"), facts=res.facts)
    return rec, requested


def codes(rec) -> set[str]:
    return {f.code for f in rec.findings}


ASK = E(90, "ask", 5)


def test_quiet_call_every_gate_present_defaults_and_no_silent_pass():
    rec, requested = run(ex(ASK))
    assert requested == set()
    gates = {g.gate.value: g.status.value for g in rec.gates}
    assert gates == {"G1": "NA", "G2": "NA", "G3": "PASS", "G4": "NA", "G5": "NA", "G6": "NA", "G7": "OUT_OF_SCOPE",
                     "G8": "NA", "G9": "NA"}
    assert rec.verdict.value == "MEETS_BAR" and codes(rec) == set()
    inconclusive = {c.code for c in rec.checks if c.status.value == "INCONCLUSIVE"}
    assert inconclusive == {"RES-11"}  # loop_similarity PENDING (B-11): never PASS by default
    assert rec.evaluability.status.value == "PARTIAL" and rec.within_scope_complete is False


def test_res02_no_ask_when_warranted_and_stage():
    assert "RES-02" in codes(run(ex())[0])
    rec, _ = run(ex(call_frame={"stage_hint": "unknown"}))
    assert any(c.code == "RES-02" and c.status.value == "INCONCLUSIVE" and c.reason_codes[0].value == "STAGE_UNKNOWN"
               for c in rec.checks)
    rec, _ = run(ex(call_frame={"stage_hint": "pre_due"}))
    assert any(c.code == "RES-02" and c.status.value == "NA" for c in rec.checks)


@pytest.mark.parametrize(("severity", "status"), [("EXPLICIT_SELF_HARM|CONTINUED_COLLECTION", "FAIL"),
                                                  ("DISTRESS_NONEXPLICIT", "NA"), ("CANNOT_DETERMINE", "FAIL"),
                                                  ("NOT_A_WORD", "FAIL")])
def test_g6_cue_severity_and_collection(severity, status):
    rec, requested = run(ex(E(1, "vulnerability_cue", 4), E(2, "ask", 5)), [("J-G6", "E1", severity)])
    assert ("J-G6", "E1") in requested
    g6 = rec.gate("G6")
    assert g6.status.value == status
    if status == "FAIL":
        assert g6.critical_status.value == "SUSPECTED"  # MEDIUM ceiling; CANNOT_DETERMINE -> SUSPECTED, never PASS


def test_g6_care_route_before_collection_passes():
    rec, _ = run(ex(E(1, "vulnerability_cue", 4), E(2, "route_action", 5, route="callback", responds_to=["E1"])),
                 [("J-G6", "E1", "MEDICAL_EMERGENCY|PROTOCOL_FOLLOWED")])
    assert rec.gate("G6").status.value == "PASS"
    assert "CALLBACK_AGREED" in {d.value for d in rec.outcome.dispositions}


@pytest.mark.parametrize(("answer", "gate", "status"), [("EXAGGERATED_ABSOLUTE", "G2", "FAIL"),
                                                        ("INTIMIDATING", "G3", "FAIL"),
                                                        ("PERMITTED_FACTUAL", "G2", "PASS"),
                                                        ("CANNOT_DETERMINE", "G2", "FAIL")])
def test_jcons_other_consequence(answer, gate, status):
    c = E(1, "consequence_statement", 5, category="other_consequence", negated=False)
    rec, requested = run(ex(c, ASK), [("J-CONS", "E1", answer)])
    assert ("J-CONS", "E1") in requested and rec.gate(gate).status.value == status


def test_prohibited_category_needs_no_judgment():
    c = E(1, "consequence_statement", 5, category="inform_family_employer_references", negated=False)
    rec, requested = run(ex(c, ASK))
    assert not any(j == "J-CONS" for j, _ in requested)
    assert (rec.gate("G3").status.value, rec.gate("G2").status.value) == ("FAIL", "PASS")


@pytest.mark.parametrize(("answer", "has"), [("NOT_ACKNOWLEDGED", True), ("ACKNOWLEDGED", False)])
def test_und01_jreg(answer, has):
    rec, requested = run(ex(E(1, "payment_claim", 4), ASK), [("J-REG", "E1", answer)])
    assert ("J-REG", "E1") in requested and ("UND-01" in codes(rec)) is has


def test_und01_registered_needs_no_judgment():
    ack = E(2, "acknowledgment", 5, kind="acknowledge", responds_to=["E1"])
    _, requested = run(ex(E(1, "payment_claim", 4), ack, ASK))
    assert ("J-REG", "E1") not in requested


def test_terminal_trigger_is_inconclusive():
    rec, requested = run(ex(E(1, "payment_claim", 8), ASK), ni=F.make_ni(SP, turns=TURNS[:8]))
    assert ("J-REG", "E1") not in requested
    assert any(c.code == "UND-01" and c.status.value == "INCONCLUSIVE" and
               c.reason_codes[0].value == "NO_AGENT_TURN_AFTER_TRIGGER" for c in rec.checks)


@pytest.mark.parametrize(("trigger", "action"), [("hardship_reason", "FIX"), ("payment_claim", "REMEDIATE")])
def test_res01_jpath(trigger, action):
    ack = E(2, "acknowledgment", 5, kind="acknowledge", responds_to=["E1"])
    rec, requested = run(ex(E(1, trigger, 4), ack, E(3, "ask", 5), E(4, "ask", 7)), [("J-PATH", "E1", "B")])
    res01 = next(f for f in rec.findings if f.code == "RES-01")
    assert ("J-PATH", "E1") in requested and res01.action_type.value == action
    assert {e.turn for e in res01.evidence} == {5, 7}


def test_res04_route_and_registration():
    sr = E(1, "settlement_request", 4)
    offer = E(2, "offer", 5, offer_type="settlement", responds_to=["E1"], accepted_turn=6)
    rec, _ = run(ex(sr, offer, ASK))
    f = next(f for f in rec.findings if f.code == "RES-04")
    assert f.attribution.primary.value == "AGENT_BEHAVIOR"  # registration override (offer responds to the request)
    assert rec.gate("G4").critical_status.value == "CONFIRMED"
    assert rec.tags.dangerous_win.value == "CRITICAL"  # accepted offer (positive outcome) + a fired gate
    rec, _ = run(ex(sr, E(2, "route_action", 5, route="callback", responds_to=["E1"]), ASK))
    assert "RES-04" not in codes(rec)


def _commitment(**kw: Any) -> dict:
    return {"id": "C1", "proposed_turn": 6, "confirmed_turn": 8, "date_raw": "12 tareekh",
            "date_norm": "2026-10-12", "amount_raw": "5,000", "amount_norm": 5000, "firmness": "firm",
            "readback_turn": 7, "readback_values_raw": "22 tareekh 5,000", "borrower_confirmation_quote": "haan", **kw}


def test_und03_digit_mismatch_and_positive_outcome():
    rec, _ = run(ex(ASK, commitments=[_commitment()]))
    assert "UND-03" in codes(rec)
    assert rec.outcome.positive and rec.outcome.commitment_turn == 8 and rec.outcome.firmness.value == "firm"


def test_und03_restated_value_and_words():
    rec, _ = run(ex(ASK, commitments=[_commitment(borrower_confirmation_quote="haan 22 tareekh")]))
    assert "UND-03" not in codes(rec)
    rec, _ = run(ex(ASK, commitments=[_commitment(readback_values_raw="baais tareekh", date_raw="barah tareekh")]))
    assert any(c.code == "UND-03" and c.status.value == "INCONCLUSIVE" for c in rec.checks)  # B-04 numerals


def test_com01_and_com03():
    rec, _ = run(ex(ASK, commitments=[_commitment(amount_raw=None, amount_norm=None, readback_values_raw="22 tareekh")]))
    assert "COM-01" in codes(rec)
    rec, _ = run(ex(ASK, commitments=[_commitment(readback_turn=None, readback_values_raw=None)]))
    assert "COM-03" in codes(rec) and next(f for f in rec.findings if f.code == "COM-03").severity.value == "MAJOR"
    cb = E(1, "route_action", 5, route="callback")
    rec, _ = run(ex(cb, ASK))
    assert next(f for f in rec.findings if f.code == "COM-03").severity.value == "MINOR"  # callback, no read-back
    rec, _ = run(ex(cb, E(2, "readback", 7), ASK))
    assert "COM-03" not in codes(rec)


@pytest.mark.parametrize(("answer", "has"), [("SOFT", True), ("FIRM", False), ("CANNOT_DETERMINE", False)])
def test_com02_jfirm(answer, has):
    c = _commitment(firmness="soft", hedge_quote="stub borrower replies", readback_values_raw="12 tareekh 5,000")
    rec, requested = run(ex(ASK, commitments=[c]), [("J-FIRM", "C1", answer)])
    assert ("J-FIRM", "C1") in requested and ("COM-02" in codes(rec)) is has
    assert rec.outcome.positive is (answer == "FIRM")
    if answer == "CANNOT_DETERMINE":
        assert any(c.code == "COM-02" and c.status.value == "INCONCLUSIVE" for c in rec.checks)


def test_com05_digits_and_words():
    k = E(1, "situational_constraint", 4, quote="stub borrower speaks")
    c = _commitment(readback_values_raw="12 tareekh 5,000")
    rec, _ = run(ex(k, ASK, commitments=[c], call_frame={}),
                 [("J-CONSTR", "E1", "NOT_NEEDED")])
    assert any(ch.code == "COM-05" and ch.status.value == "INCONCLUSIVE" for ch in rec.checks)  # no digits
    turns = list(TURNS)
    turns[3] = ("BORROWER", "stub borrower salary 20 tareekh")
    ni = F.make_ni(SP, turns=tuple(turns))
    k = E(1, "situational_constraint", 4, quote="stub borrower salary 20 tareekh")
    rec, _ = run(ex(k, ASK, commitments=[c]), [("J-CONSTR", "E1", "NOT_NEEDED")], ni=ni)
    assert "COM-05" in codes(rec)


def test_trt01_over_asking():
    events = [E(1, "inability", 4, strength="explicit")] + [E(10 + i, "ask", t) for i, t in enumerate((5, 7, 9))]
    ack = E(2, "acknowledgment", 5, kind="acknowledge", responds_to=["E1"])
    rec, _ = run(ex(*events, ack), [("J-PATH", "E1", "A")])
    f = next(f for f in rec.findings if f.code == "TRT-01")
    assert f.anchor_turn == 9 and f.confidence.value == "HIGH"


@pytest.mark.parametrize(("answer", "has"), [("NO", True), ("YES", False), ("NOT_NEEDED", False)])
def test_trt02_jconstr(answer, has):
    rec, _ = run(ex(E(1, "situational_constraint", 4), ASK), [("J-CONSTR", "E1", answer)])
    assert ("TRT-02" in codes(rec)) is has


def test_trt03_language_mismatch_and_hinglish_compatibility():
    langs = dict(LANGS, **{"4": "en", "6": "en", "7": "hi", "9": "hi"})
    rec, _ = run(ex(ASK, turn_languages=langs))
    assert "TRT-03" not in codes(rec)  # hi-en agent turn before the switch is compatible with en
    langs = dict(LANGS, **{"3": "hi", "4": "en", "6": "en", "7": "hi", "9": "hi"})
    assert "TRT-03" in codes(run(ex(ASK, turn_languages=langs))[0])
    rec, _ = run(ex(ASK, turn_languages={}))
    assert any(c.code == "TRT-03" and c.status.value == "INCONCLUSIVE" for c in rec.checks)


@pytest.mark.parametrize(("answer", "has"), [("ABSENT", True), ("PRESENT", False)])
def test_pol01a_org_statement(answer, has):
    rec, requested = run(ex(ASK, call_frame={"agent_org_statement": {"turn": 5, "quote": "x"}}),
                         [("J-G8P", "caller_identifies_org", answer)])
    assert ("J-G8P", "caller_identifies_org") in requested
    assert any(f.code == "POL-01" and f.sub_rule == "POL-01a" for f in rec.findings) is has


def test_acc04_after_stated_value_else_und04():
    q = E(1, "material_question", 4)
    val = {"value_id": "V1", "type": "payable_total", "amount_norm": 5000, "raw": "5,000", "turn": 3,
           "quote": "stub agent states dues 5,000"}
    rec, _ = run(ex(q, ASK, agent_stated_values=[val]), [("J-Q", "E1", "DEFLECTED")])
    assert "ACC-04" in codes(rec) and "UND-04" not in codes(rec)
    rec, _ = run(ex(q, ASK), [("J-Q", "E1", "UNANSWERED")])
    assert "UND-04" in codes(rec)


def test_invalid_or_missing_answers_are_cannot_determine():
    rec, _ = run(ex(E(1, "payment_claim", 4), ASK), [("J-REG", "E1", "MAYBE")])
    assert any(c.code == "UND-01" and c.status.value == "INCONCLUSIVE" for c in rec.checks)
    rec, _ = run(ex(E(1, "payment_claim", 4), ASK), [])
    assert "UND-01" not in codes(rec)


def test_und12_inconclusive_only_after_a_commitment():
    rec, _ = run(ex(E(1, "ask", 9), commitments=[_commitment(readback_values_raw="12 tareekh 5,000")]))
    assert any(c.code == "UND-12" and c.status.value == "INCONCLUSIVE" for c in rec.checks)


def test_yes_no_vocabulary_survives_yaml():
    from ignosis_eval.evaluators.judgement import judgment_text, judgment_vocabulary, JudgmentRequest

    assert judgment_vocabulary(SP, "J-CONSTR")[0][:2] == ["YES", "NO"]
    assert judgment_vocabulary(SP, "J-RIGHTS")[0] == ["YES", "NO", "AMBIGUOUS"]
    text = judgment_text(SP, [JudgmentRequest(judgment="J-RIGHTS", target="E1", turns=[4])])
    assert "True" not in text and "'YES'" in text


def test_parse_values():
    v = parse_values("5 October ko 4,500 rupaye")
    assert (v.amounts, v.days, v.months) == ((4500,), (5,), (10,))
    assert parse_values("saat October ko 8,400").days == ()  # number words need the B-04 numerals lexicon
