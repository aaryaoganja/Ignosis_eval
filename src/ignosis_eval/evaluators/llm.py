"""LLM client interface, retrying/recording wrapper, and the (deliberately unwired) real backend.

Every request and every response attempt is appended to the TraceSink and persisted by the runner as
llm_requests.jsonl / llm_responses.jsonl. Structured `payload` metadata (used only by the mock backend)
is logged as a SHA-256, not verbatim.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ignosis_eval.contracts.run_manifest import RetryPolicy
from ignosis_eval.evaluators.base import TraceSink


class LLMError(RuntimeError):
    pass


class LLMTransientError(LLMError):
    """Retryable failure (rate limit, timeout, 5xx)."""


@dataclass
class LLMRequest:
    request_id: str
    task: str
    model_id: str
    temperature: float
    max_output_tokens: int
    messages: list[dict[str, str]]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_log(self) -> dict[str, Any]:
        meta = {k: v for k, v in self.metadata.items() if k != "payload"}
        if "payload" in self.metadata:
            blob = json.dumps(self.metadata["payload"], sort_keys=True, default=str).encode("utf-8")
            meta["payload_sha256"] = hashlib.sha256(blob).hexdigest()
        return {"request_id": self.request_id, "task": self.task, "model_id": self.model_id,
                "temperature": self.temperature, "max_output_tokens": self.max_output_tokens,
                "messages": self.messages, "metadata": meta}


@dataclass
class LLMResponse:
    request_id: str
    model_id: str
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    stop_reason: str = "end_turn"


class LLMClient(ABC):
    backend: str
    model_id: str

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse: ...


class RecordingLLMClient:
    """Applies the retry policy and records every request/attempt into the trace sink."""

    def __init__(self, client: LLMClient, policy: RetryPolicy, trace: TraceSink,
                 sleep: Callable[[float], None] = time.sleep):
        self.client, self.policy, self.trace, self.sleep = client, policy, trace, sleep

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.trace.requests.append(request.to_log())
        self.trace.usage["llm_calls"] += 1
        delay = self.policy.backoff_initial_s
        for attempt in range(1, self.policy.max_attempts + 1):
            self.trace.usage["llm_attempts"] += 1
            t0 = time.perf_counter()
            try:
                resp = self.client.complete(request)
            except LLMTransientError as exc:
                self.trace.responses.append({"request_id": request.request_id, "attempt": attempt, "ok": False,
                                             "error": f"{type(exc).__name__}: {exc}",
                                             "latency_ms": round(1000 * (time.perf_counter() - t0), 3)})
                if attempt == self.policy.max_attempts:
                    raise LLMError(f"request {request.request_id} failed after {attempt} attempts") from exc
                self.trace.usage["retries"] += 1
                self.sleep(delay)
                delay *= self.policy.backoff_multiplier
                continue
            self.trace.usage["input_tokens"] += resp.input_tokens
            self.trace.usage["output_tokens"] += resp.output_tokens
            self.trace.responses.append({"request_id": request.request_id, "attempt": attempt, "ok": True,
                                         "model_id": resp.model_id, "content": resp.content,
                                         "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
                                         "latency_ms": resp.latency_ms, "stop_reason": resp.stop_reason})
            return resp
        raise AssertionError("unreachable")


class AnthropicLLMClient(LLMClient):
    """Real backend placeholder. Intentionally NOT wired in the infrastructure phase.

    Wiring a model (and writing real prompts) is the next phase; keeping this unimplemented guarantees no
    run in this phase can be mistaken for a measured evaluator result.
    """

    backend = "anthropic"

    def __init__(self, model_id: str, **_: Any):
        self.model_id = model_id
        raise NotImplementedError(
            "The real LLM backend is intentionally not wired in the infrastructure phase. "
            "Use --llm-backend mock. Prompt work and model wiring are a later, separate task.")

    def complete(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError
