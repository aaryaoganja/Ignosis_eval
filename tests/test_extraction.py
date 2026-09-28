"""B's extraction contract (AJ-07, FP-08): every explicit field, wrong-type rejection, drift against
rubric.yaml › extraction_schema, and the evidence verifier's handling of `responds_to`. Synthetic stubs only."""

from __future__ import annotations

import typing
from datetime import date

import pytest
from pydantic import ValidationError

import factories as F
from ignosis_eval.contracts import extraction as X
from ignosis_eval.contracts.extraction import Commitment, ExtractedEvent, ExtractionOutput, IdentityCheck, StatedValue
from ignosis_eval.engine.extraction import verify_extraction
from ignosis_eval.engine.finalize import DerivationLog

SP = F.spec()
SCHEMA = SP.rubric["extraction_schema"]
VOCAB = SP.rubric["extraction_vocabulary"]


def lit(t) -> list:
    return list(typing.get_args(t))


def ev(eid="e1", etype="ask", turn=1, role="AGENT", quote=None, **kw) -> ExtractedEvent:
    return ExtractedEvent(id=eid, type=etype, turn=turn, quote=quote or f"stub agent line {turn}", role=role,
                          confidence="HIGH", **kw)


# ================================================================================ drift against the rubric
def test_literals_match_rubric_extraction_schema():
    assert SCHEMA["version"] == X.EXTRACTION_SCHEMA == ExtractionOutput().schema_version
    assert lit(X.DisclosureItem) == SCHEMA["account_disclosure"]["items"]
    assert lit(X.AckKind) == SCHEMA["acknowledgment"]["kind"]
    assert lit(X.OfferType) == SCHEMA["offer"]["offer_type"]
    assert lit(X.PaymentStatusValue) == SCHEMA["payment_status_assertion"]["value"]
    cat = SCHEMA["consequence_statement"]["category"]
    assert lit(X.ConsequenceCategory) == cat["permitted"] + cat["prohibited"] + cat["other"]
    assert lit(X.StatedValueType) == SCHEMA["agent_stated_values"]["type"] == VOCAB["agent_stated_value_types"]
    assert list(StatedValue.model_fields) == SCHEMA["agent_stated_values"]["fields"]
    assert lit(X.IdentityResult) == SCHEMA["identity_checks"]["result"] == VOCAB["identity_check_result"]
    assert list(X.RESPONDS_TO_ALLOWED_ON) == SCHEMA["responds_to"]["allowed_on"]
    assert SCHEMA["ai_identity_statement"]["claims_human"] == [True, False, None]


def test_literals_match_vocabulary_and_profile():
    assert lit(X.RouteValue) == VOCAB["route_values"]
    assert lit(X.Strength) == VOCAB["borrower_event_strength"]
    assert list(Commitment.model_fields) == VOCAB["commitment_fields"]
    assert lit(Commitment.model_fields["firmness"].annotation.__args__[0]) == VOCAB["firmness"]
    offers = SP.profile["offers"]
    assert set(lit(X.OfferType)) - {"other_term_change"} == set(
        offers["not_ai_authorized"] + offers["authority_unknown"] + offers["allowed"])
    cons = SP.profile["consequences"]
    cat = SCHEMA["consequence_statement"]["category"]
    assert cat["permitted"] == cons["permitted_categories"] and cat["prohibited"] == list(cons["prohibited_categories"])
    assert SCHEMA["account_disclosure"]["items"] == SP.profile["identity_verification"]["must_precede"]
    for t in X.RESPONDS_TO_ALLOWED_ON:
        assert t in VOCAB["agent_event_types"]


# ================================================================================ every explicit field
@pytest.mark.parametrize("etype,fields", [
    ("acknowledgment", {"kind": "acknowledge"}),
    ("acknowledgment", {"kind": "clarify"}),
    ("account_disclosure", {"items": ["loan_existence"]}),
    ("account_disclosure", {"items": ["amount", "overdue_status", "loan_details"]}),
    ("ai_identity_statement", {"claims_human": True}),
    ("ai_identity_statement", {"claims_human": False}),
    ("ai_identity_statement", {}),  # claims_human null = evasive
    ("offer", {"offer_type": "other_term_change"}),
    ("payment_status_assertion", {"value": "received"}),
    ("payment_status_assertion", {"value": "received", "basis_stated": "stub basis quote"}),
    ("consequence_statement", {"category": "late_fee_per_terms", "negated": False}),
    ("consequence_statement", {"category": "other_consequence", "negated": True}),
    ("route_action", {"route": "stop_honored"}),
    ("route_action", {"route": "escalation"}),
])
def test_valid_type_specific_fields(etype, fields):
    e = ev(etype=etype, **fields)
    for k, v in fields.items():
        assert getattr(e, k) == v


def test_correction_fields():
    out = ExtractionOutput(events=[ev("c1", "correction", 3, corrects=["v1"], new_value_id="v2")],
                           agent_stated_values=[StatedValue(id="v1", type="payable_total", amount_norm=10, turn=1,
                                                            quote="stub agent line 1"),
                                                StatedValue(id="v2", type="payable_total", amount_norm=20, turn=3,
                                                            quote="stub agent line 3")])
    assert out.event("c1").corrects == ["v1"] and out.event("c1").new_value_id == "v2"


def test_responds_to_is_a_list_of_ids():
    e = ev("a1", "acknowledgment", 3, kind="acknowledge", responds_to=["b1", "b2"])
    assert e.responds_to == ["b1", "b2"]
    assert ev().responds_to == []


@pytest.mark.parametrize("etype,fields,msg", [
    ("offer", {"kind": "acknowledge", "offer_type": "discount"}, "must not carry"),   # field on the wrong type
    ("ask", {"claims_human": True}, "must not carry"),
    ("acknowledgment", {"offer_type": "discount", "kind": "clarify"}, "must not carry"),
    ("offer", {}, "requires"),                                                      # missing required field
    ("account_disclosure", {}, "requires"),
    ("consequence_statement", {"category": "arrest"}, "requires"),                  # negated is required
    ("payment_status_assertion", {"basis_stated": "x"}, "requires"),
    ("route_action", {}, "requires"),
    ("correction", {}, "requires"),
    ("account_disclosure", {"items": []}, "non-empty set"),
    ("account_disclosure", {"items": ["amount", "amount"]}, "non-empty set"),
])
def test_wrong_or_missing_fields_rejected(etype, fields, msg):
    with pytest.raises(ValidationError, match=msg):
        ev(etype=etype, **fields)


@pytest.mark.parametrize("etype,fields", [
    ("offer", {"offer_type": "loan_waiver"}),
    ("account_disclosure", {"items": ["organization_name"]}),
    ("acknowledgment", {"kind": "agree"}),
    ("payment_status_assertion", {"value": "maybe"}),
    ("consequence_statement", {"category": "fine", "negated": False}),
    ("route_action", {"route": "voicemail"}),
])
def test_unknown_enum_values_rejected(etype, fields):
    with pytest.raises(ValidationError):
        ev(etype=etype, **fields)


def test_stated_values_and_identity_checks():
    StatedValue(id="v1", type="other_charge", amount_norm=5, turn=1, quote="q")
    StatedValue(id="v2", type="due_date", date_norm=date(2026, 1, 5), turn=1, quote="q")
    with pytest.raises(ValidationError, match="amount_norm or date_norm"):
        StatedValue(id="v3", type="emi", turn=1, quote="q")
    with pytest.raises(ValidationError):
        StatedValue(id="v4", type="penalty", amount_norm=1, turn=1, quote="q")
    IdentityCheck(id="i1", turn=2, quote="q", result="unclear")
    with pytest.raises(ValidationError):
        IdentityCheck(id="i2", turn=2, quote="q", result="maybe")


def test_output_level_references():
    v = StatedValue(id="v1", type="payable_total", amount_norm=10, turn=1, quote="q")
    with pytest.raises(ValidationError, match="duplicate ids"):
        ExtractionOutput(events=[ev("v1")], agent_stated_values=[v])
    with pytest.raises(ValidationError, match="component_of"):
        ExtractionOutput(agent_stated_values=[v.model_copy(update={"component_of": "zz"})])
    with pytest.raises(ValidationError, match="unknown stated values"):
        ExtractionOutput(events=[ev("c1", "correction", 2, corrects=["nope"])], agent_stated_values=[v])


def test_check_vocabulary():
    out = ExtractionOutput(events=[ev("b1", "human_request", 2, "BORROWER", quote="q"),
                                   ev("a1", "ask", 3, strength="explicit"), ev("z1", "made_up_type", 3)])
    errs = out.check_vocabulary(SP)
    assert any("b1 requires strength" in e for e in errs) and any("a1 must not carry strength" in e for e in errs)
    assert any("unknown event type made_up_type" in e for e in errs)


def test_json_schema_exported():
    from ignosis_eval.schemas import CONTRACTS
    assert CONTRACTS["extraction"] is ExtractionOutput
    assert (F.REPO / "schemas" / "extraction.schema.json").exists()


# ================================================================================ evidence verifier
def _ni():
    return F.make_ni(SP)  # 1 A alpha, 2 B beta, 3 A gamma, 4 B delta, 5 A epsilon


def _b(eid, turn, etype="human_request"):
    quote = {2: "stub borrower line beta", 4: "stub borrower line delta"}[turn]
    return ExtractedEvent(id=eid, type=etype, turn=turn, quote=quote, role="BORROWER", confidence="HIGH",
                          strength="explicit")


def _a(eid, turn, etype="acknowledgment", **kw):
    quote = {1: "stub agent line alpha", 3: "stub agent line gamma", 5: "stub agent line epsilon"}[turn]
    if etype == "acknowledgment":
        kw.setdefault("kind", "acknowledge")
    return ExtractedEvent(id=eid, type=etype, turn=turn, quote=quote, role="AGENT", confidence="HIGH", **kw)


def test_verifier_drops_invalid_responds_to_ids():
    log = DerivationLog()
    x = ExtractionOutput(events=[
        _b("b1", 2), _b("b2", 4),
        _a("a1", 3, responds_to=["b1", "b2", "ghost", "a0"]),  # b2 is later; ghost missing; a0 not borrower
        _a("a0", 1),
        _a("q1", 5, etype="ask", responds_to=["b1"]),       # ask is not an allowed responder
    ])
    out = verify_extraction(x, _ni(), SP, log)
    assert out.event("a1").responds_to == ["b1"]
    assert out.event("q1").responds_to == []
    dropped = [e for e in log.entries if e["step"] == "extraction-verifier"]
    assert {d["target"] for d in dropped} == {"a1", "q1"}


def test_verifier_drops_unverifiable_quotes_and_dependent_items():
    x = ExtractionOutput(
        events=[_b("b1", 2), ExtractedEvent(id="bad", type="ask", turn=3, quote="words the agent never said",
                                            role="AGENT", confidence="HIGH"),
                ExtractedEvent(id="c1", type="correction", turn=5, quote="stub agent line epsilon", role="AGENT",
                               confidence="HIGH", corrects=["v2"])],
        agent_stated_values=[StatedValue(id="v1", type="payable_total", amount_norm=10, turn=1,
                                         quote="stub agent line alpha"),
                             StatedValue(id="v2", type="emi", amount_norm=4, turn=3, quote="not in the transcript",
                                         component_of="v1"),
                             StatedValue(id="v3", type="late_fee", amount_norm=1, turn=3, quote="stub agent line gamma",
                                         component_of="v1")],
        identity_checks=[IdentityCheck(id="i1", turn=2, quote="stub borrower line beta", result="affirmed"),
                         IdentityCheck(id="i2", turn=4, quote="fabricated", result="affirmed")])
    out = verify_extraction(x, _ni(), SP)
    assert [e.id for e in out.events] == ["b1"]            # 'bad' quote fails; c1 loses its only corrected value
    assert [v.id for v in out.agent_stated_values] == ["v1", "v3"]
    assert [c.id for c in out.identity_checks] == ["i1"]


def test_verifier_rejects_role_mismatch():
    x = ExtractionOutput(events=[ExtractedEvent(id="b1", type="human_request", turn=3, quote="stub agent line gamma",
                                                role="BORROWER", confidence="HIGH", strength="explicit")])
    assert verify_extraction(x, _ni(), SP).events == []
