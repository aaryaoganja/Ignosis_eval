"""K0 keyword floor — the simplest possible baseline. No LLM, no attribution reasoning, no dimensions.

Any candidate evaluator must beat K0 on the safety metrics to justify its cost. K0 attributes every
finding as 'undetermined' (keywords cannot establish cause) and never scores dimensions.
"""

from __future__ import annotations

from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.enums import EvaluatorArchitecture
from ignosis_eval.contracts.evaluation_record import EvaluationRecord
from ignosis_eval.contracts.run_manifest import EvaluatorRunConfig
from ignosis_eval.evaluators.base import NO_RETRY, EvaluationContext, Evaluator, config_hash
from ignosis_eval.evaluators.builder import RecordBuilder
from ignosis_eval.evaluators.heuristics import judge
from ignosis_eval.evaluators.judgement import apply_judgement


class KeywordFloorK0(Evaluator):
    name, version, architecture = "k0-keyword-floor", "0.1.0", EvaluatorArchitecture.K0

    def run_config(self) -> EvaluatorRunConfig:
        cfg = {"name": self.name, "version": self.version, "lexicon": "evaluators/heuristics.py"}
        return EvaluatorRunConfig(name=self.name, version=self.version, architecture=self.architecture,
                                  llm_backend="none", model_id=None, temperature=None, retry_policy=NO_RETRY,
                                  config_hash=config_hash(cfg))

    def evaluate(self, inp: CanonicalInput, ctx: EvaluationContext) -> EvaluationRecord:
        b = RecordBuilder(self.info(), inp, ctx.profile, ctx.profile_sha256, ctx.rep_seed)
        apply_judgement(b, judge(inp, keyword_only=True))
        return b.build(None)
