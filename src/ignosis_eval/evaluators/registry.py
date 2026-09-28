"""System factory used by the runner."""

from __future__ import annotations

from pathlib import Path

from ignosis_eval.contracts.enums import System
from ignosis_eval.evaluators.judgement import RuleEngine
from ignosis_eval.evaluators.k0 import KeywordFloorK0
from ignosis_eval.evaluators.llm import AnthropicLLMClient, LLMClient
from ignosis_eval.evaluators.mock_llm import ReplayLLMClient
from ignosis_eval.evaluators.pipelines import APlusDeriver, EvaluatorA, EvaluatorB
from ignosis_eval.spec.loader import Spec


def build_llm_client(backend: str, *, replay_dir: Path | None = None, model_id: str | None = None) -> LLMClient:
    if backend == "mock_replay":
        if replay_dir is None:
            raise ValueError("mock_replay backend needs --replay-dir")
        return ReplayLLMClient(replay_dir, model_id=model_id or "mock-replay/0")
    if backend == "anthropic":
        return AnthropicLLMClient(model_id or "PENDING-B-05")
    raise ValueError(f"unknown LLM backend {backend!r}")


def build_systems(systems: list[System], spec: Spec, *, llm_backend: str = "mock_replay",
                  replay_dir: Path | None = None, model_id: str | None = None, max_tokens: int | None = None,
                  rule_engine: RuleEngine | None = None, sleep=None) -> dict[System, object]:
    out: dict[System, object] = {}
    client = None
    if System.A in systems or System.B in systems:
        client = build_llm_client(llm_backend, replay_dir=replay_dir, model_id=model_id)
    for s in systems:
        if s is System.K0:
            out[s] = KeywordFloorK0()
        elif s is System.A:
            out[s] = EvaluatorA(client, spec, max_tokens=max_tokens, sleep=sleep)  # type: ignore[arg-type]
        elif s is System.A_PLUS:
            if System.A not in systems:
                raise ValueError("A+ is derived from A: include A in the run")
            out[s] = APlusDeriver(spec)
        elif s is System.B:
            out[s] = EvaluatorB(client, spec, rule_engine=rule_engine, max_tokens=max_tokens, sleep=sleep)  # type: ignore[arg-type]
    return out
