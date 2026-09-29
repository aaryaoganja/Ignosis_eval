"""B's extraction contract (rubric.yaml 1.2-mvp › extraction_schema, AJ-07): every explicit field, wrong-type
rejection, drift against the rubric, and the evidence verifier's handling of turn speakers and `responds_to`.
Synthetic stubs only."""

from __future__ import annotations

import re
import typing
from datetime import date

import pytest
from pydantic import ValidationError

import factories as F
from ignosis_eval.contracts import extraction as X
from ignosis_eval.contracts.extraction import (
    CallFrame,
    Commitment,
    ExtractedEvent,
    ExtractionOutput,
    IdentityCheck,
    StatedValue,
)
from ignosis_eval.engine.extraction import verify_extraction
from ignosis_eval.engine.finalize import DerivationLog

SP = F.spec()
SCHEMA = SP.rubric["extraction_schema"]
VOCAB = SP.rubric["extraction_vocabulary"]


def lit(t) -> list:
    return list(typing.get_args(t))


def alternatives(text: str) -> list[str]:
    """'a | b | c    # comment' -> [a, b, c] (the rubric writes field types as strings)."""
    return [p.strip() for p in text.split("#")[0].split("|")]


def bracketed(text: str) -> list[str]:
    """'one of [a, b]' / 'non-empty array from [a, b]' -> [a, b]."""
    m = re.search(r"\[([^\]]*)\]", text)
    assert m, text
    return [p.strip() for p in m.group(1).split(",")]


def ev(eid="E1", etype="ask", turn=1, quote=None, **kw) -> ExtractedEvent:
    return ExtractedEvent(event_id=eid, type=etype, turn=turn, quote=quote or f"stub line {turn}", source="supplied",
                          confidence="HIGH", **kw)


def sv(vid="V1", vtype="payable_total", turn=1, amount=10, **kw) -> StatedValue:
    return StatedValue(value_id=vid, type=vtype, amount_norm=amount, turn=turn, raw="stub", quote="q", **kw)


# ================================================================================ drift against the rubric
def test_version_is_the_exported_schema_version():
    assert X.EXTRACTION_SCHEMA == ExtractionOutput().schema_version == "extraction/2.0.0"


def test_event_common_matches_rubric():
    common = SCHEMA["event_common"]
    assert list(common) == ["event_id", "type", "turn", "quote", "source", "confidence"]
    assert X.EVENT_ID_PATTERN in common["event_id"]
    assert lit(X.EventSource) == alternatives(common["source"])
    assert "the speaker of the turn must match the event side (borrower/agent)" in common["turn"]
    fields = list(ExtractedEvent.model_fields)
    assert fields[:6] == ["event_id", "type", "turn", "quote", "source", "confidence"]
    assert "role" not in fields  # the side follows from the type


def test_event_types_match_vocabulary():
    assert list(X.BORROWER_EVENT_TYPES) == VOCAB["borrower_event_types"]
    assert list(X.AGENT_EVENT_TYPES) == VOCAB["agent_event_types"]
    assert lit(X.Strength) == VOCAB["borrower_event_strength"]
    strength = SCHEMA["borrower_event_fields"]["strength"]
    assert lit(X.Strength) == alternatives(strength.split("(")[0])
    assert all(t in strength for t in X.STRENGTH_REQUIRED_ON) and "optional otherwise" in strength


def test_type_specific_fields_match_rubric():
    assert list(X.RESPONDS_TO_CARRIED_BY) == SCHEMA["responds_to"]["carried_by"]
    assert lit(X.AckKind) == alternatives(SCHEMA["acknowledgment"]["kind"])
    assert lit(X.DisclosureItem) == bracketed(SCHEMA["account_disclosure"]["items"])
    assert list(SCHEMA["account_disclosure"]["definitions"]) == lit(X.DisclosureItem)
    assert alternatives(SCHEMA["ai_identity_statement"]["claims_human"]) == ["true", "false", "null"]
    assert lit(X.OfferType) == bracketed(SCHEMA["offer"]["offer_type"])
    assert SCHEMA["offer"]["accepted_turn"].startswith("int | null")
    assert lit(X.PaymentStatusValue) == alternatives(SCHEMA["payment_status_assertion"]["value"])
    assert lit(X.ConsequenceCategory) == bracketed(SCHEMA["consequence_statement"]["category"])
    item = SCHEMA["agent_stated_values"]["item"]
    assert list(item) == ["value_id", "type", "amount_norm", "date_norm", "raw", "turn", "quote", "component_of"]
    assert set(StatedValue.model_fields) == set(item)
    assert X.VALUE_ID_PATTERN in item["value_id"] and "payable_total" in item["component_of"]
    assert lit(X.StatedValueType) == VOCAB["agent_stated_value_types"]
    assert set(SCHEMA["correction"]) == {"corrects", "new_value_id"}


def test_vocabulary_and_profile_literals():
    assert lit(X.RouteValue) == VOCAB["route_values"]
    assert list(Commitment.model_fields) == VOCAB["commitment_fields"]
    assert lit(X.Firmness) == VOCAB["firmness"]
    assert list(X.CallFrame.model_fields) == VOCAB["call_frame"]
    assert lit(X.IdentityResult) == VOCAB["identity_check_result"]
    assert lit(X.StageHint) == VOCAB["stage_hint"]
    offers = SP.profile["offers"]
    assert set(lit(X.OfferType)) - {"other_term_change"} == set(
        offers["not_ai_authorized"] + offers["authority_unknown"] + offers["allowed"])
    cons = SP.profile["consequences"]
    assert set(lit(X.ConsequenceCategory)) == set(cons["permitted_categories"]) | set(cons["prohibited_categories"]) \
        | {"other_consequence"}
    assert lit(X.DisclosureItem) == SP.profile["identity_verification"]["must_precede"]
    for t in X.RESPONDS_TO_CARRIED_BY:
        assert t in VOCAB["agent_event_types"]


# ================================================================================ every explicit field
@pytest.mark.parametrize("etype,fields", [
    ("acknowledgment", {"kind": "acknowledge"}),
    ("acknowledgment", {"kind": "clarify"}),
    ("account_disclosure", {"items": ["loan_existence"]}),
    ("account_disclosure", {"items": ["amount", "overdue_status", "loan_details"]}),
    ("ai_identity_statement", {"claims_human": True}),
    ("ai_identity_statement", {"claims_human": False}),
    ("ai_identity_statement", {}),  # claims_human null = evasive or neither
    ("offer", {"offer_type": "other_term_change"}),
    ("offer", {"offer_type": "discount", "accepted_turn": 4}),
    ("payment_status_assertion", {"value": "received"}),
    ("payment_status_assertion", {"value": "received", "basis_stated": "stub basis quote"}),
    ("consequence_statement", {"category": "late_fee_per_terms", "negated": False}),
    ("consequence_statement", {"category": "other_consequence", "negated": True}),
    ("route_action", {"route": "stop_honored"}),
    ("route_action", {"route": "escalation"}),
    ("human_request", {"strength": "ambiguous"}),
    ("payment_claim", {}),  # strength optional on other borrower events
    ("payment_claim", {"strength": "implicit"}),
])
def test_valid_type_specific_fields(etype, fields):
    e = ev(etype=etype, **fields)
    for k, v in fields.items():
        assert getattr(e, k) == v


def test_event_side_follows_type():
    assert ev(etype="payment_claim").side.value == "BORROWER" and ev(etype="ask").side.value == "AGENT"


def test_correction_fields_are_single_value_ids():
    out = ExtractionOutput(events=[ev("E1", "correction", 3, corrects="V1", new_value_id="V2")],
                           agent_stated_values=[sv("V1"), sv("V2", amount=20, turn=3)])
    assert out.event("E1").corrects == "V1" and out.event("E1").new_value_id == "V2"
    with pytest.raises(ValidationError):
        ev("E1", "correction", 3, corrects=["V1"], new_value_id="V2")


def test_responds_to_is_a_list_of_event_ids():
    e = ev("E3", "acknowledgment", 3, kind="acknowledge", responds_to=["E1", "E2"])
    assert e.responds_to == ["E1", "E2"]
    assert ev().responds_to == []


@pytest.mark.parametrize("etype,fields,msg", [
    ("offer", {"kind": "acknowledge", "offer_type": "discount"}, "must not carry"),   # field on the wrong type
    ("ask", {"claims_human": True}, "must not carry"),
    ("acknowledgment", {"offer_type": "discount", "kind": "clarify"}, "must not carry"),
    ("ask", {"accepted_turn": 3}, "must not carry"),
    ("ask", {"strength": "explicit"}, "must not carry strength"),                    # borrower field on agent event
    ("human_request", {}, "requires strength"),
    ("stop_request", {}, "requires strength"),
    ("offer", {}, "requires"),                                                      # missing required field
    ("account_disclosure", {}, "requires"),
    ("consequence_statement", {"category": "arrest"}, "requires"),                  # negated is required
    ("payment_status_assertion", {"basis_stated": "x"}, "requires"),
    ("route_action", {}, "requires"),
    ("correction", {"corrects": "V1"}, "requires"),                                 # new_value_id is required
    ("account_disclosure", {"items": []}, "non-empty set"),
    ("account_disclosure", {"items": ["amount", "amount"]}, "non-empty set"),
    ("made_up_type", {}, "unknown event type"),
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


@pytest.mark.parametrize("field,value", [("event_id", "e1"), ("event_id", "E"), ("event_id", "V1"),
                                         ("source", "llm"), ("source", None)])
def test_event_common_patterns(field, value):
    with pytest.raises(ValidationError):
        ExtractedEvent.model_validate({"event_id": "E1", "type": "ask", "turn": 1, "quote": "q",
                                       "source": "supplied", "confidence": "HIGH", field: value})


def test_stated_values_and_call_frame():
    sv("V1", "other_charge", amount=5)
    StatedValue(value_id="V2", type="due_date", date_norm=date(2026, 1, 5), turn=1, raw="stub", quote="q")
    StatedValue(value_id="V3", type="emi", turn=1, raw="stub", quote="q")  # both normalized values may be null
    for bad in ({"value_id": "v4"}, {"type": "penalty"}, {"raw": None}):
        with pytest.raises(ValidationError):
            StatedValue.model_validate({"value_id": "V4", "type": "emi", "amount_norm": 1, "turn": 1, "raw": "stub",
                                        "quote": "q", **bad})
    frame = CallFrame(identity_checks=[IdentityCheck(turn=2, quote="q", result="unclear")], stage_hint="overdue",
                      non_conversation=False)
    assert frame.identity_checks[0].result == "unclear"
    with pytest.raises(ValidationError):
        IdentityCheck(turn=2, quote="q", result="maybe")
    with pytest.raises(ValidationError):
        CallFrame(stage_hint="late")
    with pytest.raises(ValidationError):
        CallFrame.model_validate({"unknown_field": 1})


def test_output_level_references():
    v = sv("V1")
    with pytest.raises(ValidationError, match="duplicate event_ids"):
        ExtractionOutput(events=[ev("E1"), ev("E1")])
    with pytest.raises(ValidationError, match="duplicate value_ids"):
        ExtractionOutput(agent_stated_values=[v, v])
    with pytest.raises(ValidationError, match="component_of"):
        ExtractionOutput(agent_stated_values=[v.model_copy(update={"component_of": "V9"})])
    with pytest.raises(ValidationError, match="payable_total"):  # component_of must name a payable_total
        ExtractionOutput(agent_stated_values=[sv("V1", "emi"), sv("V2", "late_fee", component_of="V1")])
    with pytest.raises(ValidationError, match="unknown stated values"):
        ExtractionOutput(events=[ev("E1", "correction", 2, corrects="V7", new_value_id="V1")], agent_stated_values=[v])


def test_check_vocabulary():
    out = ExtractionOutput(events=[ev("E1", "human_request", 2, strength="explicit"), ev("E2", "ask", 3)])
    assert out.check_vocabulary(SP) == []


def test_json_schema_exported():
    from ignosis_eval.schemas import CONTRACTS
    assert CONTRACTS["extraction"] is ExtractionOutput
    assert (F.REPO / "schemas" / "extraction.schema.json").exists()


# ================================================================================ evidence verifier
def _ni():
    return F.make_ni(SP)  # 1 A alpha, 2 B beta, 3 A gamma, 4 B delta, 5 A epsilon


def _b(eid, turn, etype="human_request"):
    quote = {2: "stub borrower line beta", 4: "stub borrower line delta"}[turn]
    return ev(eid, etype, turn, quote=quote, strength="explicit")


def _a(eid, turn, etype="acknowledgment", **kw):
    quote = {1: "stub agent line alpha", 3: "stub agent line gamma", 5: "stub agent line epsilon"}[turn]
    if etype == "acknowledgment":
        kw.setdefault("kind", "acknowledge")
    return ev(eid, etype, turn, quote=quote, **kw)


def test_verifier_removes_invalid_responds_to_ids():
    log = DerivationLog()
    x = ExtractionOutput(events=[
        _b("E1", 2), _b("E2", 4),
        _a("E3", 3, responds_to=["E1", "E2", "E99", "E4"]),  # E2 is later; E99 missing; E4 not a borrower event
        _a("E4", 1),
        _a("E5", 5, etype="ask", responds_to=["E1"]),        # ask does not carry responds_to
    ])
    out = verify_extraction(x, _ni(), SP, log)
    assert out.event("E3").responds_to == ["E1"]
    assert out.event("E5").responds_to == []
    dropped = [e for e in log.entries if e["step"] == "extraction-verifier"]
    assert {d["target"] for d in dropped} == {"E3", "E5"}


def test_verifier_drops_unverifiable_quotes_and_dependent_items():
    x = ExtractionOutput(
        events=[_b("E1", 2), ev("E2", "ask", 3, quote="words the agent never said"),
                ev("E3", "correction", 5, quote="stub agent line epsilon", corrects="V2", new_value_id="V3")],
        agent_stated_values=[
            StatedValue(value_id="V1", type="payable_total", amount_norm=10, turn=1, raw="s",
                        quote="stub agent line alpha"),
            StatedValue(value_id="V2", type="emi", amount_norm=4, turn=3, raw="s", quote="not in the transcript",
                        component_of="V1"),
            StatedValue(value_id="V3", type="emi", amount_norm=1, turn=3, raw="s", quote="stub agent line gamma",
                        component_of="V1")],
        call_frame=CallFrame(identity_checks=[IdentityCheck(turn=2, quote="stub borrower line beta", result="affirmed"),
                                              IdentityCheck(turn=4, quote="fabricated", result="affirmed")]))
    out = verify_extraction(x, _ni(), SP)
    assert [e.event_id for e in out.events] == ["E1"]      # E2 quote fails; E3 loses a corrected value
    assert [v.value_id for v in out.agent_stated_values] == ["V1", "V3"]
    assert [c.turn for c in out.call_frame.identity_checks] == [2]


def test_verifier_rejects_turn_speaker_not_matching_the_event_side():
    """event_common.turn: the speaker of the turn must match the event side (borrower/agent)."""
    x = ExtractionOutput(events=[ev("E1", "human_request", 3, quote="stub agent line gamma", strength="explicit"),
                                 ev("E2", "ask", 2, quote="stub borrower line beta")])
    assert verify_extraction(x, _ni(), SP).events == []


def test_verifier_checks_the_declared_source_text():
    """event_common.source: an `asr` quote needs ASR text for the turn; a TRANSCRIPT unit has none."""
    x = ExtractionOutput(events=[ExtractedEvent(event_id="E1", type="ask", turn=1, quote="stub agent line alpha",
                                                source="asr", confidence="HIGH")])
    assert verify_extraction(x, _ni(), SP).events == []
