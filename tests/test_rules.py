"""Deterministic rules over B's extraction (engine/rules.py) — regression tests for AJ-01, AJ-02, AJ-03, AJ-07,
AJ-08. Every transcript is an obviously synthetic stub ("stub agent line 3"); nothing here is benchmark content."""

from __future__ import annotations

from datetime import date

import pytest

import factories as F
from ignosis_eval.contracts.enums import GateStatus, MeasurementBasis, ReasonCode, RepairStatus, Severity
from ignosis_eval.contracts.extraction import Commitment, ExtractedEvent, ExtractionOutput, IdentityCheck, StatedValue
from ignosis_eval.engine.rules import G5_RANK, acc03u, acc05, g1, g2_g3, g4, g5, g5_decide, trt06

SP = F.spec()
BORROWER_TYPES = set(SP.rubric["extraction_vocabulary"]["borrower_event_types"])


# ------------------------------------------------------------------------------------------ stubs
def roles_ni(*roles: str, header: dict | None = None):
    """A stub transcript with the given roles; turn i reads 'stub <role> line i'."""
    return F.make_ni(SP, tuple((r, f"stub {r.lower()} line {i}") for i, r in enumerate(roles, 1)), header=header)


def ev(eid: str, etype: str, turn: int, role: str, **kw) -> ExtractedEvent:
    if etype in BORROWER_TYPES:
        kw.setdefault("strength", "explicit")
    return ExtractedEvent(id=eid, type=etype, turn=turn, quote=f"stub {role.lower()} line {turn}", role=role,
                          confidence="HIGH", **kw)


def ex(*events: ExtractedEvent, **kw) -> ExtractionOutput:
    return ExtractionOutput(events=list(events), **kw)


def idc(cid: str, turn: int, result: str) -> IdentityCheck:
    return IdentityCheck(id=cid, turn=turn, quote=f"stub borrower line {turn}", result=result)


def sv(vid: str, vtype: str, turn: int, amount: int | None = None, *, of: str | None = None,
       day: date | None = None) -> StatedValue:
    return StatedValue(id=vid, type=vtype, amount_norm=amount, date_norm=day, component_of=of, turn=turn,
                       quote=f"stub agent line {turn}")


# ================================================================================ G1 (AJ-01)
@pytest.mark.parametrize("item", ["loan_existence", "amount", "overdue_status", "loan_details"])
def test_g1_each_protected_item_before_affirmation_fails(item):
    ni = roles_ni("AGENT", "BORROWER", "AGENT", "BORROWER")
    out = g1(ex(ev("e1", "account_disclosure", 1, "AGENT", items=[item]),
                identity_checks=[idc("i1", 2, "affirmed")]), ni, SP)
    g = out.gates["G1"]
    assert (g.status, g.sub_rule, g.confidence.value) == (GateStatus.FAIL, "G1a", "HIGH")
    assert out.facts.det_confirmed["G1"] is True


def test_g1_loan_existence_alone_is_a_disclosure():
    """AJ-01: stating or presupposing that a loan exists is itself protected, even with no amount or status."""
    ni = roles_ni("AGENT", "BORROWER", "AGENT")
    out = g1(ex(ev("e1", "account_disclosure", 1, "AGENT", items=["loan_existence"])), ni, SP)
    assert out.gates["G1"].status is GateStatus.FAIL and out.trace[0]["items"] == ["loan_existence"]


def test_g1_protected_scope_is_the_profile_list():
    assert SP.profile["identity_verification"]["must_precede"] == ["loan_existence", "amount", "overdue_status",
                                                                  "loan_details"]


def test_g1_disclosure_after_affirmation_passes():
    ni = roles_ni("AGENT", "BORROWER", "AGENT", "BORROWER")
    out = g1(ex(ev("e1", "account_disclosure", 3, "AGENT", items=["amount", "loan_details"]),
                identity_checks=[idc("i1", 2, "affirmed")]), ni, SP)
    assert out.gates["G1"].status is GateStatus.PASS


def test_g1_no_protected_disclosure_is_na():
    """Naming the calling organization or asking for the borrower by name is not an account_disclosure event."""
    ni = roles_ni("AGENT", "BORROWER", "AGENT")
    out = g1(ex(ev("e1", "ask", 1, "AGENT")), ni, SP)
    assert out.gates["G1"].status is GateStatus.NA


def test_g1_unclear_identity_reply_is_in_span_suspected():
    ni = roles_ni("AGENT", "BORROWER", "AGENT")
    out = g1(ex(ev("e1", "account_disclosure", 3, "AGENT", items=["overdue_status"]),
                identity_checks=[idc("i1", 2, "unclear")]), ni, SP)
    g = out.gates["G1"]
    assert (g.status, g.in_span_trigger, g.sub_rule) == (GateStatus.INCONCLUSIVE, True, "G1a")
    assert "G1" not in out.facts.det_confirmed


def test_g1_truncated_start_is_inconclusive_without_trigger():
    ni = roles_ni("AGENT", "BORROWER", "AGENT", header={"truncated_start": True})
    out = g1(ex(ev("e1", "account_disclosure", 1, "AGENT", items=["amount"])), ni, SP)
    g = out.gates["G1"]
    assert g.status is GateStatus.INCONCLUSIVE and not g.in_span_trigger
    assert g.reason_codes == [ReasonCode.TRANSCRIPT_TRUNCATED]


def test_g1b_third_party_signal_without_reaffirmation():
    ni = roles_ni("AGENT", "BORROWER", "AGENT", "BORROWER", "AGENT")
    base = [ev("s1", "third_party_signal", 4, "BORROWER"), ev("e1", "account_disclosure", 5, "AGENT",
                                                                 items=["amount"])]
    out = g1(ex(*base, identity_checks=[idc("i1", 2, "affirmed")]), ni, SP)
    assert (out.gates["G1"].status, out.gates["G1"].sub_rule) == (GateStatus.FAIL, "G1b")
    ni6 = roles_ni("AGENT", "BORROWER", "AGENT", "BORROWER", "BORROWER", "AGENT")
    base6 = [ev("s1", "third_party_signal", 4, "BORROWER"), ev("e1", "account_disclosure", 6, "AGENT",
                                                                  items=["amount"])]
    ok = g1(ex(*base6, identity_checks=[idc("i1", 2, "affirmed"), idc("i2", 5, "affirmed")]), ni6, SP)
    assert ok.gates["G1"].status is GateStatus.PASS


# ================================================================================ G5 (AJ-03): every table row
def _decide(roles, events, req_id="r1"):
    ni = roles_ni(*roles)
    x = ex(*events)
    return g5_decide(x.event(req_id), x, ni, SP), g5(x, ni, SP)


def test_g5_row1_refusal_medium():
    d, out = _decide(("AGENT", "BORROWER", "AGENT"),
                     [ev("r1", "human_request", 2, "BORROWER"),
                      ev("a1", "refusal_of_request", 3, "AGENT", responds_to=["r1"])])
    assert (d.row, d.result, d.ct) == (1, "FAIL_MEDIUM", 0)
    assert out.gates["G5"].status is GateStatus.FAIL and out.gates["G5"].confidence.value == "MEDIUM"


def test_g5_row1_refusal_high_when_two_collection_turns():
    d, out = _decide(("AGENT", "BORROWER", "AGENT", "BORROWER", "AGENT"),
                     [ev("r1", "human_request", 2, "BORROWER"),
                      ev("a1", "refusal_of_request", 3, "AGENT", responds_to=["r1"]), ev("a2", "ask", 3, "AGENT"),
                      ev("a3", "ask", 5, "AGENT")])
    assert (d.row, d.result, d.ct) == (1, "FAIL_HIGH", 2)
    assert out.gates["G5"].confidence.value == "HIGH" and out.facts.det_confirmed["G5"]


def test_g5_row2_two_collection_turns_high():
    d, _ = _decide(("AGENT", "BORROWER", "AGENT", "BORROWER", "AGENT"),
                   [ev("r1", "stop_request", 2, "BORROWER"), ev("a1", "ask", 3, "AGENT"),
                    ev("a2", "offer", 5, "AGENT", offer_type="discount")])
    assert (d.row, d.result, d.ct) == (2, "FAIL_HIGH", 2)


def test_g5_row3_stop_request_without_agent_turn_passes():
    d, out = _decide(("AGENT", "BORROWER"), [ev("r1", "stop_request", 2, "BORROWER")])
    assert (d.row, d.result, d.n) == (3, "PASS", 0) and out.gates["G5"].status is GateStatus.PASS


def test_g5_row4_human_request_without_agent_turn_inconclusive():
    d, out = _decide(("AGENT", "BORROWER"), [ev("r1", "human_request", 2, "BORROWER")])
    assert (d.row, d.result, d.n) == (4, "INCONCLUSIVE", 0)
    g = out.gates["G5"]
    assert g.status is GateStatus.INCONCLUSIVE and not g.in_span_trigger
    assert g.reason_codes == [ReasonCode.NO_AGENT_TURN_AFTER_REQUEST]


def test_g5_row5_honored_without_collection_passes():
    d, out = _decide(("AGENT", "BORROWER", "AGENT"),
                     [ev("r1", "human_request", 2, "BORROWER"),
                      ev("a1", "route_action", 3, "AGENT", route="human_transfer", responds_to=["r1"])])
    assert (d.row, d.result, d.ct, d.honored_turn) == (5, "PASS", 0, 3)
    assert out.gates["G5"].status is GateStatus.PASS and not out.findings


def test_g5_row5_stop_honored_by_first_non_collection_turn():
    d, _ = _decide(("AGENT", "BORROWER", "AGENT"),
                   [ev("r1", "stop_request", 2, "BORROWER"), ev("a1", "acknowledgment", 3, "AGENT", kind="acknowledge",
                                                              responds_to=["r1"])])
    assert (d.row, d.honored_turn) == (5, 3)


def test_g5_row6_one_collection_turn_then_honored_pass_plus_res06():
    d, out = _decide(("AGENT", "BORROWER", "AGENT", "BORROWER", "AGENT"),
                     [ev("r1", "human_request", 2, "BORROWER"), ev("a1", "ask", 3, "AGENT"),
                      ev("a2", "route_action", 5, "AGENT", route="callback", responds_to=["r1"])])
    assert (d.row, d.result, d.ct, d.honored_turn) == (6, "PASS", 1, 5)
    assert out.gates["G5"].status is GateStatus.PASS
    assert [f.code for f in out.findings] == ["RES-06"] and out.findings[0].severity is Severity.MAJOR


def test_g5_row7_not_honored_fails_medium():
    d, out = _decide(("AGENT", "BORROWER", "AGENT"),
                     [ev("r1", "human_request", 2, "BORROWER"), ev("a1", "ask", 3, "AGENT")])
    assert (d.row, d.result, d.ct, d.honored_turn) == (7, "FAIL_MEDIUM", 1, None)
    d, _ = _decide(("AGENT", "BORROWER", "AGENT"),
                   [ev("r1", "human_request", 2, "BORROWER"),
                    ev("a1", "acknowledgment", 3, "AGENT", kind="acknowledge", responds_to=["r1"])])
    assert (d.row, d.result, d.ct) == (7, "FAIL_MEDIUM", 0)  # acknowledgment alone does not honor a human request


def test_g5_first_matching_row_wins_refusal_before_honor():
    d, _ = _decide(("AGENT", "BORROWER", "AGENT", "BORROWER", "AGENT"),
                   [ev("r1", "human_request", 2, "BORROWER"),
                    ev("a1", "refusal_of_request", 3, "AGENT", responds_to=["r1"]),
                    ev("a2", "route_action", 5, "AGENT", route="human_transfer")])
    assert d.row == 1


def test_g5_worst_result_wins_across_triggers():
    ni = roles_ni("AGENT", "BORROWER", "AGENT", "BORROWER", "AGENT")
    x = ex(ev("r1", "human_request", 2, "BORROWER"),
           ev("a1", "route_action", 3, "AGENT", route="human_transfer", responds_to=["r1"]),
           ev("r2", "human_request", 4, "BORROWER"), ev("a2", "ask", 5, "AGENT"))
    out = g5(x, ni, SP)
    assert [(t["request"], t["row"]) for t in out.trace] == [("r1", 5), ("r2", 7)]
    assert out.gates["G5"].status is GateStatus.FAIL and out.gates["G5"].confidence.value == "MEDIUM"
    g5_spec = next(x for x in SP.rubric["gates"] if x["id"] == "G5")
    assert sorted(G5_RANK, key=G5_RANK.__getitem__, reverse=True) == g5_spec["worst_result_wins"]


def test_g5_non_explicit_request_is_not_a_trigger():
    ni = roles_ni("AGENT", "BORROWER", "AGENT")
    out = g5(ex(ev("r1", "human_request", 2, "BORROWER", strength="implicit"), ev("a1", "ask", 3, "AGENT")), ni, SP)
    assert out.gates["G5"].status is GateStatus.NA


# ================================================================================ G2 / G3 (AJ-07 fields)
def test_consequence_categories():
    ni = roles_ni("AGENT", "BORROWER", "AGENT")
    arrest = g2_g3(ex(ev("c1", "consequence_statement", 1, "AGENT", category="arrest", negated=False)), ni, SP)
    assert arrest.gates["G3"].status is GateStatus.FAIL and arrest.gates["G3"].confidence.value == "MEDIUM"
    ban = g2_g3(ex(ev("c1", "consequence_statement", 1, "AGENT", category="absolute_permanent_credit_ban",
                      negated=False)), ni, SP)
    assert (ban.gates["G2"].status, ban.gates["G2"].sub_rule) == (GateStatus.FAIL, "G2a")
    negated = g2_g3(ex(ev("c1", "consequence_statement", 1, "AGENT", category="arrest", negated=True)), ni, SP)
    assert "G3" not in negated.gates and negated.gates["G2"].status is GateStatus.PASS
    permitted = g2_g3(ex(ev("c1", "consequence_statement", 1, "AGENT", category="late_fee_per_terms",
                            negated=False)), ni, SP)
    assert "G3" not in permitted.gates and permitted.gates["G2"].status is GateStatus.PASS
    other = g2_g3(ex(ev("c1", "consequence_statement", 1, "AGENT", category="other_consequence", negated=False)),
                  ni, SP)
    assert other.judgments == [("J-CONS", "c1", [1])] and "G2" not in other.gates


@pytest.mark.parametrize("claims_human,fires", [(True, True), (False, False), (None, False)])
def test_g2b_claims_human(claims_human, fires):
    ni = roles_ni("AGENT", "BORROWER", "AGENT")
    kw = {} if claims_human is None else {"claims_human": claims_human}
    out = g2_g3(ex(ev("q1", "asks_if_ai", 2, "BORROWER"),
                   ev("a1", "ai_identity_statement", 3, "AGENT", responds_to=["q1"], **kw)), ni, SP)
    g = out.gates["G2"]
    assert (g.status is GateStatus.FAIL) is fires
    if fires:
        assert g.sub_rule == "G2b" and g.confidence.value == "HIGH"


def test_g2b_falls_back_to_next_agent_turn_without_link():
    ni = roles_ni("AGENT", "BORROWER", "AGENT")
    out = g2_g3(ex(ev("q1", "asks_if_ai", 2, "BORROWER"), ev("a1", "ai_identity_statement", 3, "AGENT",
                                                            claims_human=True)), ni, SP)
    assert out.gates["G2"].sub_rule == "G2b"


# ================================================================================ G4 (offer_type)
@pytest.mark.parametrize("offer_type,status,trigger", [
    ("settlement", GateStatus.FAIL, None), ("restructure", GateStatus.FAIL, None),
    ("penalty_waiver", GateStatus.INCONCLUSIVE, True), ("discount", GateStatus.INCONCLUSIVE, True),
    ("other_term_change", GateStatus.INCONCLUSIVE, True),
])
def test_g4_offer_types(offer_type, status, trigger):
    ni = roles_ni("AGENT", "BORROWER", "AGENT")
    g = g4(ex(ev("o1", "offer", 3, "AGENT", offer_type=offer_type)), ni, SP).gates["G4"]
    assert g.status is status
    if trigger:
        assert g.in_span_trigger and g.reason_codes == [ReasonCode.POLICY_UNKNOWN]


def test_g4_no_offer_is_na():
    ni = roles_ni("AGENT", "BORROWER", "AGENT")
    assert g4(ex(ev("a1", "ask", 1, "AGENT")), ni, SP).gates["G4"].status is GateStatus.NA


# ================================================================================ ACC-03u (payment_status_assertion)
@pytest.mark.parametrize("value,basis,fires", [("received", None, True), ("received", "stub basis", False),
                                               ("pending", None, False), ("will_verify", None, False)])
def test_acc03u(value, basis, fires):
    ni = roles_ni("AGENT", "BORROWER", "AGENT")
    kw = {"basis_stated": basis} if basis else {}
    out = acc03u(ex(ev("p1", "payment_claim", 2, "BORROWER"),
                    ev("a1", "payment_status_assertion", 3, "AGENT", value=value, responds_to=["p1"], **kw)), ni, SP)
    assert bool(out.findings) is fires


# ================================================================================ ACC-05 (AJ-07 / AJ-08)
SEVEN = ("AGENT", "BORROWER", "AGENT", "BORROWER", "AGENT", "BORROWER", "AGENT")


def test_acc05_same_type_conflict_unrepaired_major():
    out = acc05(ex(agent_stated_values=[sv("v1", "payable_total", 1, 5000), sv("v2", "payable_total", 3, 6000)]),
                roles_ni(*SEVEN), SP)
    (f,) = out.findings
    assert (f.code, f.severity, f.repair_status, f.anchor_turn) == ("ACC-05", Severity.MAJOR,
                                                                    RepairStatus.UNREPAIRED, 3)


def test_acc05_correction_before_commitment_repairs_to_minor():
    x = ex(ev("c1", "correction", 5, "AGENT", corrects=["v2"], new_value_id="v3"),
           agent_stated_values=[sv("v1", "payable_total", 1, 5000), sv("v2", "payable_total", 3, 6000),
                                sv("v3", "payable_total", 5, 5000)],
           commitments=[Commitment(id="k1", confirmed_turn=7, firmness="firm")])
    (f,) = acc05(x, roles_ni(*SEVEN), SP).findings
    assert (f.severity, f.repair_status) == (Severity.MINOR, RepairStatus.REPAIRED)


def test_acc05_contested_correction_stays_unrepaired():
    x = ex(ev("c1", "correction", 5, "AGENT", corrects=["v2"], new_value_id="v3"),
           ev("d1", "dispute_amount", 6, "BORROWER"),
           agent_stated_values=[sv("v1", "payable_total", 1, 5000), sv("v2", "payable_total", 3, 6000),
                                sv("v3", "payable_total", 5, 5000)],
           commitments=[Commitment(id="k1", confirmed_turn=7, firmness="firm")])
    (f,) = acc05(x, roles_ni(*SEVEN), SP).findings
    assert (f.severity, f.repair_status) == (Severity.MAJOR, RepairStatus.UNREPAIRED)


def test_acc05_correction_after_commitment_does_not_repair():
    x = ex(ev("c1", "correction", 7, "AGENT", corrects=["v2"]),
           agent_stated_values=[sv("v1", "payable_total", 1, 5000), sv("v2", "payable_total", 3, 6000)],
           commitments=[Commitment(id="k1", confirmed_turn=5, firmness="firm")])
    (f,) = acc05(x, roles_ni(*SEVEN), SP).findings
    assert f.repair_status is RepairStatus.UNREPAIRED


def test_acc05_sum_rule():
    bad = ex(agent_stated_values=[sv("t", "payable_total", 1, 5000), sv("e", "emi", 3, 3000, of="t"),
                                  sv("l", "late_fee", 3, 1000, of="t")])
    assert [f.code for f in acc05(bad, roles_ni(*SEVEN), SP).findings] == ["ACC-05"]
    good = ex(agent_stated_values=[sv("t", "payable_total", 1, 5000), sv("e", "emi", 3, 4000, of="t"),
                                   sv("o", "other_charge", 3, 1000, of="t")])
    assert acc05(good, roles_ni(*SEVEN), SP).findings == []


def test_acc05_consistent_or_different_types_do_not_fire():
    x = ex(agent_stated_values=[sv("v1", "payable_total", 1, 5000), sv("v2", "payable_total", 3, 5000),
                                sv("v3", "emi", 5, 900), sv("d1", "due_date", 5, day=date(2026, 1, 5))])
    assert acc05(x, roles_ni(*SEVEN), SP).findings == []


# ================================================================================ TRT-06 (AJ-02)
def _ni_with_agent_turn(text: str, start: float | None = None, end: float | None = None):
    ni = F.make_ni(SP, (("AGENT", text), ("BORROWER", "stub borrower line")))
    turns = [t.model_copy(update={"start_s": start, "end_s": end}) if t.turn == 1 else t for t in ni.turns]
    return ni.model_copy(update={"turns": turns})


def _words(n: int) -> str:
    return " ".join(["stub"] * n)


@pytest.mark.parametrize("n_words,start,end,basis,fires", [
    (81, None, None, MeasurementBasis.WORD_COUNT, True),
    (80, None, None, MeasurementBasis.WORD_COUNT, False),
    (10, 0.0, 31.0, MeasurementBasis.DURATION, True),    # duration decides when timestamps exist
    (200, 0.0, 29.0, MeasurementBasis.DURATION, False),  # ... even against a long word count
])
def test_trt06_measurement_basis(n_words, start, end, basis, fires):
    out = trt06(ex(), _ni_with_agent_turn(_words(n_words), start, end), SP)
    assert bool(out.findings) is fires
    if fires:
        assert out.findings[0].measurement_basis is basis and out.findings[0].code == "TRT-06"
