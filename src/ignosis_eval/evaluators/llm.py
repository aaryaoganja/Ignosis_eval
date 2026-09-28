"""LLM client interface, transport retries and schema retry (experiment-protocol P-3).

  * Transport errors: up to 3 retries with backoff; logged; NOT counted as retries.
  * Schema-invalid output: exactly 1 retry; if still invalid the caller emits EVALUATION_FAILED (V0 / R-03).
  * The real backend is not wired: the model snapshot id, API access and key handling are PENDING (B-05).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from ignosis_eval.contracts.run_manifest import TransportRetryPolicy
from ignosis_eval.evaluators.base import TraceSink

T = TypeVar("T")
SCHEMA_RETRY_NOTE = "The previous output did not validate against the required schema. Return only valid JSON."


class TransportError(RuntimeError):
    """Retryable transport failure (timeout, 5xx, rate limit)."""


class LLMUnavailableError(RuntimeError):
    """The LLM could not be reached after the transport retries."""


class OutputSchemaError(ValueError):
    """The model output does not validate against the structured-output schema."""


@dataclass
class LLMRequest:
    request_id: str
    task: str
    model_id: str
    temperature: float
    max_tokens: int | None
    messages: list[dict[str, str]]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_log(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "task": self.task, "model_id": self.model_id,
                "temperature": self.temperature, "max_tokens": self.max_tokens, "messages": self.messages,
                "metadata": self.metadata}


@dataclass
class LLMResponse:
    request_id: str
    model_id: str
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    stop_reason: str = "end_turn"


class LLMClient(ABC):
    backend: str
    model_id: str

    @abstractmethod
    def complete(self, request: LLMRequest, attempt: int) -> LLMResponse: ...


class RecordingClient:
    def __init__(self, client: LLMClient, policy: TransportRetryPolicy, trace: TraceSink,
                 sleep: Callable[[float], None] = time.sleep):
        self.client, self.policy, self.trace, self.sleep = client, policy, trace, sleep

    def complete(self, request: LLMRequest, schema_attempt: int) -> LLMResponse:
        self.trace.requests.append({**request.to_log(), "schema_attempt": schema_attempt})
        self.trace.usage["llm_calls"] += 1
        delay = self.policy.backoff_initial_s
        for transport_try in range(self.policy.max_retries + 1):
            t0 = time.perf_counter()
            try:
                resp = self.client.complete(request, schema_attempt)
            except TransportError as exc:
                self.trace.responses.append({"request_id": request.request_id, "schema_attempt": schema_attempt,
                                             "transport_try": transport_try, "ok": False,
                                             "error": f"{type(exc).__name__}: {exc}"})
                if transport_try == self.policy.max_retries:
                    raise LLMUnavailableError(f"{request.request_id}: transport failed after "
                                              f"{self.policy.max_retries} retries") from exc
                self.trace.usage["transport_retries"] += 1
                self.sleep(delay)
                delay *= self.policy.backoff_multiplier
                continue
            u = self.trace.usage
            u["input_tokens"] += resp.input_tokens
            u["output_tokens"] += resp.output_tokens
            u["cached_tokens"] += resp.cached_tokens
            self.trace.responses.append({"request_id": request.request_id, "schema_attempt": schema_attempt,
                                         "transport_try": transport_try, "ok": True, "model_id": resp.model_id,
                                         "content": resp.content, "input_tokens": resp.input_tokens,
                                         "output_tokens": resp.output_tokens, "cached_tokens": resp.cached_tokens,
                                         "latency_ms": round(1000 * (time.perf_counter() - t0), 3),
                                         "stop_reason": resp.stop_reason})
            return resp
        raise AssertionError("unreachable")


def structured_call(client: RecordingClient, request: LLMRequest, parse: Callable[[str], T]
                    ) -> tuple[T | None, str | None, str | None, int]:
    """One call plus at most one schema retry. Returns (parsed, raw_content, error, schema_attempts)."""
    error: str | None = None
    raw: str | None = None
    for attempt in (1, 2):
        req = request
        if attempt == 2:
            req = LLMRequest(request.request_id + "-schema-retry", request.task, request.model_id, request.temperature,
                             request.max_tokens, request.messages + [{"role": "user", "content": SCHEMA_RETRY_NOTE}],
                             dict(request.metadata))
            client.trace.usage["schema_retries"] += 1
        resp = client.complete(req, attempt)
        raw = resp.content
        try:
            return parse(resp.content), raw, None, attempt
        except (OutputSchemaError, ValueError) as exc:
            error = str(exc)[:2000]
    return None, raw, error, 2


class AnthropicLLMClient(LLMClient):
    """Real backend placeholder. The pinned snapshot id, API access, max_tokens and seed support are
    PENDING_HUMAN_SIGNOFF (B-05); prompt work is a later phase. Constructing it fails closed."""

    backend = "anthropic"

    def __init__(self, model_id: str, **_: Any):
        self.model_id = model_id
        raise NotImplementedError("LLM model snapshot and API access are PENDING (B-05); use the mock_replay backend")

    def complete(self, request: LLMRequest, attempt: int) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError
