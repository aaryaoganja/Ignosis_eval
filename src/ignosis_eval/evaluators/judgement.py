"""Evaluator B contracts: extraction output (LLM #1), rule-engine interface, judgment requests (LLM #2).

The extraction vocabulary comes from rubric.yaml › extraction_vocabulary. Event-specific attributes
(e.g. responds_to, claims_human, offer_type) are referenced by the rules but not enumerated by the spec;
they are carried in `attributes` until the extraction schema is completed in the B build phase.
The rule engine itself is the next build phase: `NotImplementedRuleEngine` fails explicitly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field, model_validator

from ignosis_eval.contracts._base import Contract, NonEmptyStr
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import Confidence, Role
from ignosis_eval.contracts.evaluation_record import RecordBody
from ignosis_eval.engine.finalize import Facts
from ignosis_eval.spec.loader import Spec


class ExtractedEvent(Contract):
    type: NonEmptyStr
    turn: int = Field(ge=1)
    quote: str
    role: Role
    confidence: Confidence  # rubric: every_event_requires [turn, quote, confidence]
    strength: Literal["explicit", "implicit", "ambiguous"] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ExtractionOutput(Contract):
    events: list[ExtractedEvent] = Field(default_factory=list)
    call_frame: dict[str, Any] = Field(default_factory=dict)
    commitments: list[dict[str, Any]] = Field(default_factory=list)
    call_end: dict[str, Any] = Field(default_factory=dict)
    turn_languages: dict[str, str] = Field(default_factory=dict)  # R-22

    def check_vocabulary(self, spec: Spec) -> list[str]:
        vocab = spec.rubric["extraction_vocabulary"]
        allowed = set(vocab["borrower_event_types"]) | set(vocab["agent_event_types"]) | set(vocab["call_frame"])
        return [f"unknown event type {e.type}" for e in self.events if e.type not in allowed]


class JudgmentRequest(Contract):
    judgment: NonEmptyStr  # J-REG, J-PATH, ... (rubric.yaml › judgments)
    target: NonEmptyStr
    turns: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _known(self) -> "JudgmentRequest":
        if not self.judgment.startswith("J-"):
            raise ValueError("judgment ids start with J-")
        return self


class JudgmentAnswer(Contract):
    judgment: NonEmptyStr
    target: NonEmptyStr
    answer: NonEmptyStr
    cited_turns: list[int] = Field(min_length=1)  # "Every answer must cite turn IDs"


class JudgmentOutput(Contract):
    answers: list[JudgmentAnswer] = Field(default_factory=list)


@dataclass
class RuleEngineResult:
    body: RecordBody
    facts: Facts = field(default_factory=Facts)
    judgments_needed: list[JudgmentRequest] = field(default_factory=list)


class RuleEngine(ABC):
    @abstractmethod
    def apply(self, extraction: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> RuleEngineResult: ...

    @abstractmethod
    def integrate(self, result: RuleEngineResult, answers: JudgmentOutput, spec: Spec) -> RuleEngineResult: ...


class NotImplementedRuleEngine(RuleEngine):
    def apply(self, extraction: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> RuleEngineResult:
        raise NotImplementedError("B rule engine (rubric codes from extraction) is the next build phase")

    def integrate(self, result: RuleEngineResult, answers: JudgmentOutput, spec: Spec) -> RuleEngineResult:
        raise NotImplementedError("B rule engine (judgment integration) is the next build phase")


def judgment_text(spec: Spec, requests: list[JudgmentRequest]) -> str:
    catalog = spec.rubric["judgments"]
    lines = []
    for r in requests:
        j = catalog.get(r.judgment, {})
        lines.append(f"- {r.judgment} on {r.target} (turns {r.turns}): {j.get('question', '')} "
                     f"Answers: {j.get('answers') or j.get('answers_severity')}")
    return "\n".join(lines)
