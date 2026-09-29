"""Evaluator B contracts: rule-engine interface and judgment requests (LLM #2).

The extraction output (LLM #1) is the typed contract `contracts/extraction.py` (rubric.yaml › extraction_schema,
AJ-07). `SpecRuleEngine` runs every MVP gate and code over the verified extraction (engine/rules.py and
engine/code_rules.py), asks for the targeted judgments the rubric assigns (rubric.yaml › judgments; one batched LLM
call), and integrates the answers. An answer outside the judgment's vocabulary, or citing no existing turn, counts
as CANNOT_DETERMINE ("check INCONCLUSIVE; for gates with in-span trigger -> SUSPECTED"). A check whose rule cannot
run is INCONCLUSIVE, never PASS. `NotImplementedRuleEngine` remains for tests of the interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, model_validator

from ignosis_eval.contracts._base import Contract, NonEmptyStr
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.evaluation_record import RecordBody
from ignosis_eval.contracts.extraction import ExtractedEvent, ExtractionOutput
from ignosis_eval.engine import code_rules
from ignosis_eval.engine.finalize import Facts
from ignosis_eval.spec.loader import Spec


class JudgmentRequest(Contract):
    judgment: NonEmptyStr  # J-REG, J-PATH, ... (rubric.yaml › judgments)
    target: NonEmptyStr
    turns: list[int] = Field(default_factory=list)
    context: str | None = None  # the trigger event (type and quote) the question is about

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
    log: list[dict] = field(default_factory=list)  # rule decisions and judgment answers (derivation log)
    state: Any = None  # engine-private state carried from apply() to integrate()


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


def _word(v: object) -> str:
    """YAML 1.1 reads the rubric's bare YES / NO answers as booleans; restore the words the rubric wrote."""
    return "YES" if v is True else "NO" if v is False else str(v)


def judgment_vocabulary(spec: Spec, judgment: str) -> tuple[list[str], list[str]]:
    """(answers, handling answers) of a judgment; only J-G6 has the second list (rubric.yaml › judgments)."""
    j = spec.rubric["judgments"].get(judgment, {})
    return ([_word(v) for v in (j.get("answers") or j.get("answers_severity") or [])],
            [_word(v) for v in (j.get("answers_handling") or [])])


def judgment_text(spec: Spec, requests: list[JudgmentRequest]) -> str:
    catalog = spec.rubric["judgments"]
    lines = []
    for r in requests:
        j = catalog.get(r.judgment, {})
        answers, handling = judgment_vocabulary(spec, r.judgment)
        fmt = f"Answers: {answers}" if not handling else \
            f"Answer as SEVERITY|HANDLING with SEVERITY in {answers} and HANDLING in {handling}"
        about = f" about {r.context}" if r.context else ""
        lines.append(f"- judgment={r.judgment} target={r.target} (turns {r.turns}){about}: {j.get('question', '')} "
                     f"{fmt}")
    return "\n".join(lines)


def _valid_answer(spec: Spec, a: JudgmentAnswer, n_turns: int) -> str:
    answers, handling = judgment_vocabulary(spec, a.judgment)
    if not all(1 <= t <= n_turns for t in a.cited_turns):
        return code_rules.CANNOT
    parts = [x for x in re.split(r"[|;,/ ]+", a.answer.strip().upper()) if x]
    if not parts or parts[0] not in answers or (handling and len(parts) > 1 and parts[1] not in handling):
        return code_rules.CANNOT
    return "|".join(parts[:2]) if handling else parts[0]


class SpecRuleEngine(RuleEngine):
    """B's rule engine (rubric.yaml 1.2-mvp; engine/code_rules.py)."""

    def apply(self, extraction: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> RuleEngineResult:
        p = code_rules.plan(extraction, ni, spec)
        requests = []
        for pend in p.pending:
            ev = extraction.event(pend.target)
            ctx = f"{ev.type} at turn {ev.turn}: \"{ev.quote}\"" if ev is not None else None
            requests.append(JudgmentRequest(judgment=pend.judgment, target=pend.target, turns=pend.turns,
                                            context=ctx))
        result = RuleEngineResult(body=RecordBody(), judgments_needed=requests, state=p)
        if not requests:
            body, facts, log = code_rules.resolve(p, {}, spec)
            result.body, result.facts, result.log = body, facts, log
        return result

    def integrate(self, result: RuleEngineResult, answers: JudgmentOutput, spec: Spec) -> RuleEngineResult:
        p = result.state
        assert isinstance(p, code_rules.Plan) and p.ni is not None
        got: dict[tuple[str, str], str] = {}
        rejected: list[dict] = []
        for a in answers.answers:
            v = _valid_answer(spec, a, len(p.ni.turns))
            if v == code_rules.CANNOT and a.answer.strip().upper() != code_rules.CANNOT:
                rejected.append({"judgment": a.judgment, "target": a.target, "answer": a.answer,
                                 "cited_turns": a.cited_turns, "change": "invalid answer -> CANNOT_DETERMINE"})
            got.setdefault((a.judgment, a.target), v)
        body, facts, log = code_rules.resolve(p, got, spec)
        return RuleEngineResult(body=body, facts=facts, judgments_needed=result.judgments_needed,
                                log=rejected + log, state=p)


__all__ = ["ExtractedEvent", "ExtractionOutput", "JudgmentAnswer", "JudgmentOutput", "JudgmentRequest",
           "NotImplementedRuleEngine", "RuleEngine", "RuleEngineResult", "SpecRuleEngine", "judgment_text",
           "judgment_vocabulary"]
