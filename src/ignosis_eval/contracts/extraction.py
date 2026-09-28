"""Evaluator B extraction output (LLM call #1) — rubric.yaml › extraction_schema (AJ-07, FP-08).

Every AJ-07 field is explicit and typed; a type-specific field on the wrong event type is a schema error.
The literal vocabularies below mirror rubric.yaml › extraction_schema / extraction_vocabulary exactly
(drift test: tests/test_extraction.py). The JSON Schema is exported to schemas/extraction.schema.json.

Validation split:
  * structural (here): field presence/types per event type, unique ids, `correction` / `component_of`
    references to existing stated values;
  * vocabulary (`check_vocabulary`): event and route types against the rubric;
  * evidence (engine/extraction.py): quotes, roles, and `responds_to` ids (invalid ids are dropped and logged).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from ignosis_eval.contracts._base import Contract, NonEmptyStr
from ignosis_eval.contracts.enums import Confidence, Role
from ignosis_eval.versions import EXTRACTION_SCHEMA

DisclosureItem = Literal["loan_existence", "amount", "overdue_status", "loan_details"]
AckKind = Literal["acknowledge", "clarify"]
OfferType = Literal["settlement", "principal_reduction", "restructure", "tenure_change", "penalty_waiver",
                    "fee_reversal", "discount", "other_term_change"]
PaymentStatusValue = Literal["received", "not_received", "pending", "will_verify"]
ConsequenceCategory = Literal[
    "late_fee_per_terms", "credit_bureau_reporting_per_terms", "legal_recourse_per_agreement_general",
    "arrest", "police", "jail", "visit_home_or_workplace", "inform_family_employer_references", "public_shaming",
    "absolute_permanent_credit_ban", "other_consequence"]
StatedValueType = Literal["payable_total", "emi", "late_fee", "due_date", "other_charge"]
RouteValue = Literal["dispute", "verification", "hardship", "human_transfer", "callback", "care_protocol",
                     "stop_honored", "escalation"]
Strength = Literal["explicit", "implicit", "ambiguous"]
IdentityResult = Literal["affirmed", "denied", "unclear"]

RESPONDS_TO_ALLOWED_ON = ("acknowledgment", "readback", "refusal_of_request", "route_action", "ai_identity_statement",
                          "payment_status_assertion", "offer")

# event type -> fields it requires / may carry (beyond event_common)
_TYPE_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    "acknowledgment": ({"kind"}, set()),
    "account_disclosure": ({"items"}, set()),
    "ai_identity_statement": (set(), {"claims_human"}),
    "offer": ({"offer_type"}, set()),
    "payment_status_assertion": ({"value"}, {"basis_stated"}),
    "consequence_statement": ({"category", "negated"}, set()),
    "route_action": ({"route"}, set()),
    "correction": ({"corrects"}, {"new_value_id"}),
}
_SPECIFIC = {"kind", "items", "claims_human", "offer_type", "value", "basis_stated", "category", "negated", "route",
             "corrects", "new_value_id"}


class ExtractedEvent(Contract):
    """event_common + the type-specific AJ-07 fields."""

    id: NonEmptyStr
    type: NonEmptyStr
    turn: int = Field(ge=1)
    quote: str
    role: Role
    confidence: Confidence
    strength: Strength | None = None  # borrower events only
    responds_to: list[NonEmptyStr] = Field(default_factory=list)
    kind: AckKind | None = None
    items: list[DisclosureItem] | None = None
    claims_human: bool | None = None  # true / false / null (evasive)
    offer_type: OfferType | None = None
    value: PaymentStatusValue | None = None
    basis_stated: str | None = None
    category: ConsequenceCategory | None = None
    negated: bool | None = None
    route: RouteValue | None = None
    corrects: list[NonEmptyStr] | None = None
    new_value_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _type_fields(self) -> "ExtractedEvent":
        required, optional = _TYPE_FIELDS.get(self.type, (set(), set()))
        present = {f for f in _SPECIFIC if getattr(self, f) is not None}
        missing = [f for f in sorted(required) if getattr(self, f) is None]
        extra = sorted(present - required - optional)
        errs = []
        if missing:
            errs.append(f"{self.type} event {self.id} requires {missing}")
        if extra:
            errs.append(f"{self.type} event {self.id} must not carry {extra}")
        if self.items is not None and (not self.items or len(set(self.items)) != len(self.items)):
            errs.append(f"account_disclosure {self.id}: items must be a non-empty set")
        if self.corrects is not None and not self.corrects:
            errs.append(f"correction {self.id}: corrects must reference at least one stated value")
        if errs:
            raise ValueError("; ".join(errs))
        return self


class IdentityCheck(Contract):
    id: NonEmptyStr
    turn: int = Field(ge=1)
    quote: str
    result: IdentityResult


class StatedValue(Contract):
    """extraction_schema.agent_stated_values item."""

    id: NonEmptyStr
    type: StatedValueType
    amount_norm: int | None = Field(default=None, ge=0)  # integer rupees
    date_norm: date | None = None
    component_of: NonEmptyStr | None = None
    turn: int = Field(ge=1)
    quote: str

    @model_validator(mode="after")
    def _value(self) -> "StatedValue":
        if self.amount_norm is None and self.date_norm is None:
            raise ValueError(f"stated value {self.id} needs amount_norm or date_norm")
        return self


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
    firmness: Literal["firm", "soft", "conditional"] | None = None
    hedge_quote: str | None = None
    readback_turn: int | None = Field(default=None, ge=1)
    readback_values_raw: str | None = None
    borrower_confirmation_quote: str | None = None


class ExtractionOutput(Contract):
    schema_version: Literal["extraction/1.0.0"] = EXTRACTION_SCHEMA
    events: list[ExtractedEvent] = Field(default_factory=list)
    identity_checks: list[IdentityCheck] = Field(default_factory=list)
    agent_stated_values: list[StatedValue] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)
    call_frame: dict[str, Any] = Field(default_factory=dict)  # agent_org_statement, non_conversation, stage_hint
    call_end: dict[str, Any] = Field(default_factory=dict)
    turn_languages: dict[str, str] = Field(default_factory=dict)  # R-22

    @model_validator(mode="after")
    def _references(self) -> "ExtractionOutput":
        errs = []
        ids = [e.id for e in self.events] + [c.id for c in self.identity_checks] + \
            [v.id for v in self.agent_stated_values] + [c.id for c in self.commitments]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            errs.append(f"duplicate ids {dup}")
        values = {v.id for v in self.agent_stated_values}
        for v in self.agent_stated_values:
            if v.component_of is not None and (v.component_of not in values or v.component_of == v.id):
                errs.append(f"stated value {v.id}: component_of {v.component_of} is not another stated value")
        for e in self.events:
            if e.type == "correction":
                bad = [r for r in (e.corrects or []) + ([e.new_value_id] if e.new_value_id else []) if r not in values]
                if bad:
                    errs.append(f"correction {e.id} references unknown stated values {bad}")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    def event(self, event_id: str) -> ExtractedEvent | None:
        return next((e for e in self.events if e.id == event_id), None)

    def check_vocabulary(self, spec: Any) -> list[str]:
        """Event types against rubric.yaml › extraction_vocabulary (types + role side)."""
        vocab = spec.rubric["extraction_vocabulary"]
        borrower, agent = set(vocab["borrower_event_types"]), set(vocab["agent_event_types"])
        errs = []
        for e in self.events:
            if e.type not in borrower | agent:
                errs.append(f"unknown event type {e.type}")
            elif e.type in borrower and e.strength is None:
                errs.append(f"borrower event {e.id} requires strength")
            elif e.type in agent and e.strength is not None:
                errs.append(f"agent event {e.id} must not carry strength")
        return errs


__all__ = ["Commitment", "ExtractedEvent", "ExtractionOutput", "IdentityCheck", "RESPONDS_TO_ALLOWED_ON",
           "StatedValue"]
