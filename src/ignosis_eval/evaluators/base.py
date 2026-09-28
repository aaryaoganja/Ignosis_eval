"""Evaluator interface shared by A, A+, B, K0 and the mocks.

Contract: `evaluate(normalized_input, ctx) -> EvaluationRecord`. Evaluators receive ONLY the normalized
Canonical Input and an EvaluationContext (repetition, per-repetition seed, profile, trace sink). They never
receive item ids, case metadata, case cards, manifests or gold, and they run inside the gold-access guard.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.enums import EvaluatorArchitecture
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, EvaluatorInfo
from ignosis_eval.contracts.profile import Profile
from ignosis_eval.contracts.run_manifest import EvaluatorRunConfig, RetryPolicy


@dataclass
class TraceSink:
    """Collects raw LLM traffic and usage for one item x repetition (persisted by the runner)."""

    requests: list[dict[str, Any]] = field(default_factory=list)
    responses: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=lambda: {"llm_calls": 0, "llm_attempts": 0, "input_tokens": 0,
                                                            "output_tokens": 0, "retries": 0})


@dataclass
class EvaluationContext:
    repetition: int
    rep_seed: int
    profile: Profile
    profile_sha256: str
    trace: TraceSink = field(default_factory=TraceSink)


def config_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class Evaluator(ABC):
    name: str
    version: str
    architecture: EvaluatorArchitecture

    @abstractmethod
    def run_config(self) -> EvaluatorRunConfig:
        """Everything the run manifest must record about this evaluator (model, prompts, retry policy, ...)."""

    @abstractmethod
    def evaluate(self, inp: CanonicalInput, ctx: EvaluationContext) -> EvaluationRecord:
        """Evaluate one normalized input. Must return a record conforming to the Evaluation Record contract."""

    def info(self) -> EvaluatorInfo:
        rc = self.run_config()
        return EvaluatorInfo(name=rc.name, version=rc.version, architecture=rc.architecture, model_id=rc.model_id,
                             prompt_hashes=rc.prompt_hashes, config_hash=rc.config_hash)


NO_RETRY = RetryPolicy(max_attempts=1, backoff_initial_s=0.0, backoff_multiplier=1.0, retry_on=[])
