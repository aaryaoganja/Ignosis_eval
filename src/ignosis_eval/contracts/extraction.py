"""Evaluator B extraction output (LLM call #1) — rubric.yaml 1.2-mvp › extraction_schema (AJ-07) and
extraction_vocabulary.

Every field the rubric defines is explicit and typed. The literal vocabularies below mirror the rubric exactly
(drift test: tests/test_extraction.py). The JSON Schema is exported to schemas/extraction.schema.json.

  * event_common: `event_id` (^E[0-9]+$, unique), `type`, `turn`, `quote`, `source` (supplied | asr), `confidence`.
    The event side (BORROWER / AGENT) follows from the type: the rubric requires "the speaker of the turn must match
    the event side (borrower/agent)", so an event carries no role of its own.
  * borrower_event_fields: `strength`, required for human_request and stop_request, optional on other borrower
    events. It is a borrower field, so an agent event carrying it is a schema error.
  * responds_to (array of event_id, carried by the seven `carried_by` types): invalid ids (not a BORROWER event, not
    earlier, or on a type that does not carry responds_to) are removed by the verifier and logged
    (engine/extraction.py), never here.
  * agent_stated_values: `value_id` (^V[0-9]+$), `raw` required, `component_of` = the value_id of a payable_total.
  * correction: `corrects` and `new_value_id` are single value_ids.
  * call_frame: agent_org_statement, identity_checks, non_conversation, stage_hint (extraction_vocabulary.call_frame).

Validation split:
  * structural (here): field presence/types per event type, id patterns and uniqueness, borrower-field placement,
    references between stated values and corrections;
  * evidence (engine/extraction.py): quotes, turn speaker vs event side, and `responds_to` ids.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from ignosis_eval.contracts._base import Contract, NonEmptyStr
from ignosis_eval.contracts.enums import Confidence, Role
from ignosis_eval.versions import EXTRACTION_SCHEMA

EVENT_ID_PATTERN = r"^E[0-9]+$"
VALUE_ID_PATTERN = r"^V[0-9]+$"

BORROWER_EVENT_TYPES = ("payment_claim", "dispute_amount", "dispute_liability", "inability", "hardship_reason",
                        "vulnerability_cue", "human_request", "stop_request", "refusal", "callback_request",
                        "settlement_request", "material_question", "situational_constraint", "language_request",
                        "third_party_signal", "asks_if_ai", "complaint_prior_conduct")
AGENT_EVENT_TYPES = ("account_disclosure", "ask", "consequence_statement", "offer", "payment_status_assertion",
                     "acknowledgment", "route_action", "readback", "promise_of_action", "refusal_of_request",
                     "ai_identity_statement", "ends_call", "correction")
STRENGTH_REQUIRED_ON = ("human_request", "stop_request")

DisclosureItem = Literal["loan_existence", "amount", "overdue_status", "loan_details"]
AckKind = Literal["acknowledge", "clarify"]
OfferType = Literal["settlement", "principal_reduction", "restructure", "tenure_change", "penalty_waiver",
                    "fee_reversal", "discount", "other_term_change"]
PaymentStatusValue = Literal["received", "not_received", "pending", "will_verify"]
ConsequenceCategory = Literal[
    "late_fee_per_terms", "credit_bureau_reporting_per_terms", "legal_recourse_per_agreement_general",
    "arrest", "police", "jail", "visit_home_or_workplace", "inform_family_employer_references", "public_shaming",
    "absolute_permanent_credit_ban", "other_consequence"]
StatedValueType = Literal["payable_total", "emi", "late_fee", "other_charge", "due_date"]
RouteValue = Literal["dispute", "verification", "hardship", "human_transfer", "callback", "care_protocol",
                     "stop_honored", "escalation"]
Strength = Literal["explicit", "implicit", "ambiguous"]
EventSource = Literal["supplied", "asr"]
IdentityResult = Literal["affirmed", "denied", "unclear"]
StageHint = Literal["pre_due", "overdue", "unknown"]
Firmness = Literal["firm", "soft", "conditional"]

RESPONDS_TO_CARRIED_BY = ("acknowledgment", "readback", "route_action", "refusal_of_request", "ai_identity_statement",
                          "payment_status_assertion", "offer")

# event type -> fields it requires / may carry (beyond event_common). `route` on route_action: the rubric's field
# vocabulary is extraction_vocabulary.route_values and its rules read "route in {...}" (RES-04, G5 honoring_event).
_TYPE_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    "acknowledgment": ({"kind"}, set()),
    "account_disclosure": ({"items"}, set()),
    "ai_identity_statement": (set(), {"claims_human"}),
    "offer": ({"offer_type"}, {"accepted_turn"}),
    "payment_status_assertion": ({"value"}, {"basis_stated"}),
    "consequence_statement": ({"category", "negated"}, set()),
    "route_action": ({"route"}, set()),
    "correction": ({"corrects", "new_value_id"}, set()),
}
_SPECIFIC = {"kind", "items", "claims_human", "offer_type", "accepted_turn", "value", "basis_stated", "category",
             "negated", "route", "corrects", "new_value_id"}


def event_side(event_type: str) -> Role:
    """BORROWER for borrower_event_types, AGENT for agent_event_types (the turn speaker must match it)."""
    if event_type in BORROWER_EVENT_TYPES:
        return Role.BORROWER
    if event_type in AGENT_EVENT_TYPES:
        return Role.AGENT
    raise ValueError(f"unknown event type {event_type!r}")


class ExtractedEvent(Contract):
    """event_common + borrower_event_fields + the type-specific AJ-07 fields."""

    event_id: str = Field(pattern=EVENT_ID_PATTERN)
    type: NonEmptyStr
    turn: int = Field(ge=1)
    quote: str
    source: EventSource
    confidence: Confidence
    strength: Strength | None = None  # borrower events only
    responds_to: list[NonEmptyStr] = Field(default_factory=list)
    kind: AckKind | None = None
    items: list[DisclosureItem] | None = None
    claims_human: bool | None = None  # true / false / null (evasive or neither)
    offer_type: OfferType | None = None
    accepted_turn: int | None = Field(default=None, ge=1)  # borrower acceptance turn
    value: PaymentStatusValue | None = None
    basis_stated: str | None = None
    category: ConsequenceCategory | None = None
    negated: bool | None = None
    route: RouteValue | None = None
    corrects: str | None = Field(default=None, pattern=VALUE_ID_PATTERN)
    new_value_id: str | None = Field(default=None, pattern=VALUE_ID_PATTERN)

    @model_validator(mode="after")
    def _type_fields(self) -> "ExtractedEvent":
        errs = []
        if self.type not in BORROWER_EVENT_TYPES and self.type not in AGENT_EVENT_TYPES:
            errs.append(f"event {self.event_id}: unknown event type {self.type!r}")
        elif self.type in AGENT_EVENT_TYPES and self.strength is not None:
            errs.append(f"agent event {self.event_id} must not carry strength (a borrower_event_field)")
        elif self.type in STRENGTH_REQUIRED_ON and self.strength is None:
            errs.append(f"{self.type} event {self.event_id} requires strength")
        required, optional = _TYPE_FIELDS.get(self.type, (set(), set()))
        present = {f for f in _SPECIFIC if getattr(self, f) is not None}
        missing = [f for f in sorted(required) if getattr(self, f) is None]
        extra = sorted(present - required - optional)
        if missing:
            errs.append(f"{self.type} event {self.event_id} requires {missing}")
        if extra:
            errs.append(f"{self.type} event {self.event_id} must not carry {extra}")
        if self.items is not None and (not self.items or len(set(self.items)) != len(self.items)):
            errs.append(f"account_disclosure {self.event_id}: items must be a non-empty set")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    @property
    def side(self) -> Role:
        return event_side(self.type)


class IdentityCheck(Contract):
    """call_frame.identity_checks item. The rubric fixes the result vocabulary (identity_check_result); G1's
    deterministic confirmation is the turn order of account_disclosure vs identity_checks(result=affirmed), so each
    check carries the turn (and quote, for evidence verification) of the borrower's reply."""

    turn: int = Field(ge=1)
    quote: str
    result: IdentityResult


class CallFrame(Contract):
    """extraction_vocabulary.call_frame. `agent_org_statement` has no typed shape in the rubric and no implemented
    rule reads it, so it is carried as opaque JSON."""

    agent_org_statement: Any = None
    identity_checks: list[IdentityCheck] = Field(default_factory=list)
    non_conversation: bool | None = None
    stage_hint: StageHint | None = None


class StatedValue(Contract):
    """extraction_schema.agent_stated_values item."""

    value_id: str = Field(pattern=VALUE_ID_PATTERN)
    type: StatedValueType
    amount_norm: int | None = Field(default=None, ge=0)  # integer rupees | null
    date_norm: date | None = None  # ISO-8601 date | null
    raw: str
    turn: int = Field(ge=1)  # agent turn
    quote: str
    component_of: str | None = Field(default=None, pattern=VALUE_ID_PATTERN)  # value_id of a payable_total | null


class Commitment(Contract):
    """extraction_vocabulary.commitment_fields (typed; firmness decides the positive outcome, AJ-09)."""

    id: NonEmptyStr
    proposed_turn: int | None = Field(default=None, ge=1)
    confirmed_turn: int | None = Field(default=None, ge=1)
    date_raw: str | None = None
    date_norm: date | None = None
    amount_raw: str | None = None
    amount_norm: int | None = Field(default=None, ge=0)
    mode_raw: str | None = None
    firmness: Firmness | None = None
    hedge_quote: str | None = None
    readback_turn: int | None = Field(default=None, ge=1)
    readback_values_raw: str | None = None
    borrower_confirmation_quote: str | None = None


class ExtractionOutput(Contract):
    schema_version: Literal["extraction/2.0.0"] = EXTRACTION_SCHEMA
    events: list[ExtractedEvent] = Field(default_factory=list)
    agent_stated_values: list[StatedValue] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)
    call_frame: CallFrame = Field(default_factory=CallFrame)
    call_end: dict[str, Any] = Field(default_factory=dict)
    turn_languages: dict[str, str] = Field(default_factory=dict)  # R-22

    @model_validator(mode="after")
    def _references(self) -> "ExtractionOutput":
        errs = []
        for label, ids in (("event_id", [e.event_id for e in self.events]),
                           ("value_id", [v.value_id for v in self.agent_stated_values]),
                           ("commitment id", [c.id for c in self.commitments])):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            if dup:
                errs.append(f"duplicate {label}s {dup}")
        values = {v.value_id: v for v in self.agent_stated_values}
        for v in self.agent_stated_values:
            total = values.get(v.component_of) if v.component_of is not None else None
            if v.component_of is not None and (total is None or total is v or total.type != "payable_total"):
                errs.append(f"stated value {v.value_id}: component_of {v.component_of} is not another stated "
                            "payable_total")
        for e in self.events:
            if e.type == "correction":
                bad = [r for r in (e.corrects, e.new_value_id) if r not in values]
                if bad:
                    errs.append(f"correction {e.event_id} references unknown stated values {bad}")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    def event(self, event_id: str) -> ExtractedEvent | None:
        return next((e for e in self.events if e.event_id == event_id), None)

    def value(self, value_id: str) -> StatedValue | None:
        return next((v for v in self.agent_stated_values if v.value_id == value_id), None)

    def check_vocabulary(self, spec: Any) -> list[str]:
        """Event and route types against the loaded rubric (drift guard; the literals above mirror it)."""
        vocab = spec.rubric["extraction_vocabulary"]
        borrower, agent = set(vocab["borrower_event_types"]), set(vocab["agent_event_types"])
        errs = []
        for e in self.events:
            if e.type not in borrower | agent:
                errs.append(f"unknown event type {e.type}")
            if e.route is not None and e.route not in vocab["route_values"]:
                errs.append(f"unknown route {e.route}")
        return errs


__all__ = ["AGENT_EVENT_TYPES", "BORROWER_EVENT_TYPES", "CallFrame", "Commitment", "ExtractedEvent",
           "ExtractionOutput", "IdentityCheck", "RESPONDS_TO_CARRIED_BY", "STRENGTH_REQUIRED_ON", "StatedValue",
           "event_side"]
