"""Evaluator B contracts: rule-engine interface and judgment requests (LLM #2).

The extraction output (LLM #1) is the typed contract `contracts/extraction.py` (rubric.yaml › extraction_schema,
AJ-07). Deterministic rules over it live in `engine/rules.py` (G1, G2b, G4, G5/RES-06, consequence categories,
ACC-03u, ACC-05, TRT-06). Assembling every rubric code into B's rule engine is the next build phase:
`NotImplementedRuleEngine` fails explicitly, so B never reports an unevaluated code as passed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pydantic import Field, model_validator

from ignosis_eval.contracts._base import Contract, NonEmptyStr
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.evaluation_record import RecordBody
from ignosis_eval.contracts.extraction import ExtractedEvent, ExtractionOutput
from ignosis_eval.engine.finalize import Facts
from ignosis_eval.spec.loader import Spec


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


__all__ = ["ExtractedEvent", "ExtractionOutput", "JudgmentAnswer", "JudgmentOutput", "JudgmentRequest",
           "NotImplementedRuleEngine", "RuleEngine", "RuleEngineResult", "judgment_text"]
