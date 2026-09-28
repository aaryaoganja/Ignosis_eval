"""Evaluator factory used by the experiment runner."""

from __future__ import annotations

from typing import Any

from ignosis_eval.evaluators.base import Evaluator
from ignosis_eval.evaluators.k0 import KeywordFloorK0
from ignosis_eval.evaluators.llm import AnthropicLLMClient, LLMClient
from ignosis_eval.evaluators.mock_llm import MockLLMClient
from ignosis_eval.evaluators.pipelines import EvaluatorA, EvaluatorAPlus, EvaluatorB

LLM_EVALUATORS = {"a": EvaluatorA, "a_plus": EvaluatorAPlus, "b": EvaluatorB}
EVALUATOR_NAMES = ("k0", *LLM_EVALUATORS)


def build_llm_client(backend: str, model_id: str | None, options: dict[str, Any]) -> LLMClient:
    if backend == "mock":
        return MockLLMClient(model_id=model_id or "mock-llm/0", noise_rate=float(options.get("noise_rate", 0.0)))
    if backend == "anthropic":
        if not model_id:
            raise ValueError("--model-id is required for the anthropic backend")
        return AnthropicLLMClient(model_id)
    raise ValueError(f"unknown LLM backend {backend!r}")


def build_evaluator(name: str, *, llm_backend: str = "mock", model_id: str | None = None,
                    temperature: float = 0.0, options: dict[str, Any] | None = None) -> Evaluator:
    options = dict(options or {})
    if name == "k0":
        if options:
            raise ValueError("k0 takes no options")
        return KeywordFloorK0()
    if name not in LLM_EVALUATORS:
        raise ValueError(f"unknown evaluator {name!r}; choose from {EVALUATOR_NAMES}")
    client = build_llm_client(llm_backend, model_id, options)
    return LLM_EVALUATORS[name](client, temperature=temperature, options=options)
