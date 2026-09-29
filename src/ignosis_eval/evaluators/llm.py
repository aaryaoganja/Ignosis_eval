"""LLM client interface, transport retries and schema retry (experiment-protocol P-3).

  * Transport errors: up to 3 retries with backoff; logged; NOT counted as retries.
  * Schema-invalid output: exactly 1 retry; if still invalid the caller emits EVALUATION_FAILED (V0 / R-03).
  * Backends: `mock_replay` (recorded fixtures), `openai` (OpenAI-compatible Chat Completions over stdlib HTTPS; inert
    until OPENAI_API_KEY and a dated, pinned snapshot are supplied) and `anthropic` (fails closed: the DEV transcripts
    are Claude-assisted, so a Claude-family evaluator would break authoring constraint 1). The provider choice and
    the pinned snapshot remain the owner's decision (B-05); nothing here selects one.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
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


class ProviderConfigError(RuntimeError):
    """The LLM provider is not configured (key, pinned snapshot): B-05 is pending. Fails closed before any call."""


class ProviderRequestError(RuntimeError):
    """A non-retryable provider error (e.g. HTTP 400/401/403/404): an infrastructure failure, never a record."""


# backend -> model family (authoring constraint 1 compares it with the transcripts' assisting family)
MODEL_FAMILIES = {"anthropic": "anthropic-claude", "openai": "openai", "mock_replay": "mock", "none": "none"}


def backend_family(backend: str) -> str:
    return MODEL_FAMILIES.get(backend, backend)


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


DATED_SNAPSHOT = re.compile(r"\d{4}-\d{2}-\d{2}$")
RETRYABLE_HTTP = (408, 409, 429)


class OpenAIChatClient(LLMClient):
    """OpenAI-compatible Chat Completions client (stdlib urllib; no SDK). Structured output via JSON mode; the parser
    validates against the contract and gets one schema retry (P-3). Configuration (fail closed):
      * an explicit model id ending in a dated snapshot (YYYY-MM-DD); aliases such as "latest" are refused (P-3);
      * OPENAI_API_KEY (or `api_key`); OPENAI_BASE_URL (or `base_url`) for a compatible endpoint.
    Transport: timeouts, connection errors, HTTP 408/409/429 and 5xx raise TransportError (retried by
    RecordingClient); any other HTTP error raises ProviderRequestError."""

    backend = "openai"

    def __init__(self, model_id: str | None, *, api_key: str | None = None, base_url: str | None = None,
                 timeout_s: float = 120.0, opener: Callable[..., Any] | None = None):
        if not model_id or "latest" in model_id.lower() or not DATED_SNAPSHOT.search(model_id):
            raise ProviderConfigError(f"model {model_id!r} is not a dated, pinned snapshot (P-3; B-05 is pending)")
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderConfigError("OPENAI_API_KEY is not set: the evaluator provider is pending (B-05)")
        self.model_id = model_id
        self._key = key
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.timeout_s = timeout_s
        self._open = opener or urllib.request.urlopen

    def payload(self, request: LLMRequest) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model_id, "messages": request.messages,
                                "temperature": request.temperature, "response_format": {"type": "json_object"}}
        if request.max_tokens:
            body["max_completion_tokens"] = request.max_tokens
        seed = request.metadata.get("seed")
        if seed is not None:
            body["seed"] = seed
        return body

    def complete(self, request: LLMRequest, attempt: int) -> LLMResponse:
        req = urllib.request.Request(f"{self.base_url}/chat/completions",
                                     data=json.dumps(self.payload(request)).encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {self._key}",
                                              "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500] if exc.fp is not None else ""
            if exc.code in RETRYABLE_HTTP or exc.code >= 500:
                raise TransportError(f"HTTP {exc.code}: {detail}") from exc
            raise ProviderRequestError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderRequestError(f"unexpected response shape: {str(data)[:500]}") from exc
        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return LLMResponse(request.request_id, str(data.get("model") or self.model_id), content,
                           input_tokens=int(usage.get("prompt_tokens") or 0),
                           output_tokens=int(usage.get("completion_tokens") or 0), cached_tokens=int(cached),
                           stop_reason=str(choice.get("finish_reason") or "stop"))
