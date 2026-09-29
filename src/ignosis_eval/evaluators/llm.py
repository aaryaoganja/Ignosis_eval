"""LLM client interface, transport retries and schema retry (experiment-protocol P-3).

  * Transport errors: up to 3 retries with backoff; logged; NOT counted as retries.
  * Schema-invalid output: exactly 1 retry; if still invalid the caller emits EVALUATION_FAILED (V0 / R-03).
  * Backends:
    - `gemini`: the evaluator provider (Google Gemini generateContent over stdlib HTTPS). Its model id and settings
      come from evaluators/provider_config.py; the key only from the runtime env GEMINI_API_KEY.
    - `openai`: an OpenAI-compatible Chat Completions client. Inert until OPENAI_API_KEY and a dated snapshot are
      given.
    - `mock_replay`: recorded fixtures.
    - `anthropic`: fails closed. The DEV transcripts are Claude-assisted, so a Claude-family evaluator would break
      authoring constraint 1.
  * Both HTTP clients share `_post_json` (error mapping, secret redaction). A provider failure never becomes a
    record verdict: it raises, and the caller writes EVALUATION_FAILED or fails the run.
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
from ignosis_eval.evaluators import provider_config
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
    """A non-retryable provider error (e.g. HTTP 400/401/403/404, a malformed body): an infrastructure failure. It
    never becomes a verdict: the caller writes EVALUATION_FAILED, and stops the run when `is_config_error`."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def is_config_error(self) -> bool:
        """Every further call would fail the same way: bad key, no access, unknown model."""
        return self.status in (401, 403, 404) or "API_KEY_INVALID" in str(self) or "API key not valid" in str(self)


# backend -> model family (authoring constraint 1 compares it with the transcripts' assisting family)
MODEL_FAMILIES = {"anthropic": "anthropic-claude", "openai": "openai", "gemini": provider_config.FAMILY,
                  "mock_replay": "mock", "none": "none"}


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
    reasoning_tokens: int = 0  # provider "thinking" tokens, included in output_tokens (billed as output)


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
                                         "reasoning_tokens": resp.reasoning_tokens,
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
_KEY_PATTERNS = (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), re.compile(r"sk-[0-9A-Za-z_\-]{20,}"))


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove the given secrets and anything shaped like a Google / OpenAI API key from provider error text."""
    for sec in secrets:
        if sec:
            text = text.replace(sec, "[REDACTED]")
    for pat in _KEY_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _post_json(opener: Callable[..., Any], url: str, body: dict[str, Any], headers: dict[str, str],
               timeout_s: float, *, secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    """POST JSON, parse JSON. Timeouts, connection errors, HTTP 408/409/429 and 5xx raise TransportError (retried by
    RecordingClient); any other HTTP error, or a body that is not a JSON object, raises ProviderRequestError. Error
    text is redacted: no secret reaches a trace, a log or an API response."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={**headers, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = redact(exc.read().decode("utf-8", "replace")[:500] if exc.fp is not None else "", secrets)
        if exc.code in RETRYABLE_HTTP or exc.code >= 500:
            raise TransportError(f"HTTP {exc.code}: {detail}") from None
        raise ProviderRequestError(f"HTTP {exc.code}: {detail}", exc.code) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise TransportError(redact(f"{type(exc).__name__}: {exc}", secrets)) from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ProviderRequestError(f"response is not JSON: {redact(raw[:300], secrets)!r}") from None
    if not isinstance(data, dict):
        raise ProviderRequestError(f"unexpected response shape: {redact(str(data)[:300], secrets)}")
    return data


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
        data = _post_json(self._open, f"{self.base_url}/chat/completions", self.payload(request),
                          {"Authorization": f"Bearer {self._key}"}, self.timeout_s, secrets=(self._key,))
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderRequestError(f"unexpected response shape: {redact(str(data)[:500], (self._key,))}") from exc
        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return LLMResponse(request.request_id, str(data.get("model") or self.model_id), content,
                           input_tokens=int(usage.get("prompt_tokens") or 0),
                           output_tokens=int(usage.get("completion_tokens") or 0), cached_tokens=int(cached),
                           stop_reason=str(choice.get("finish_reason") or "stop"))


class GeminiClient(LLMClient):
    """Google Gemini `models/{model}:generateContent` over stdlib HTTPS (no SDK); the evaluator provider.

    - Configuration comes from provider_config: model id (validated: pinned, no alias), base URL, timeout.
    - The key comes from GEMINI_API_KEY at construction, is sent only in the `x-goog-api-key` header (never in the
      URL), and is redacted from every error message. It fails closed (ProviderConfigError) before any call when
      the key or a valid model id is missing.
    - Request: `contents` (assistant -> model; consecutive same-role messages merged), `generationConfig` with
      temperature (0, P-3), seed, maxOutputTokens (when set), candidateCount 1, responseMimeType application/json.
    - Response: the text parts of the first candidate (thought parts skipped). A blocked prompt, no candidate or an
      empty text returns "" so the schema retry, then EVALUATION_FAILED, follows: never a PASS.
    - Usage: promptTokenCount -> input; candidatesTokenCount + thoughtsTokenCount -> output; cachedContentTokenCount
      -> cached. `modelVersion` (the served version) is recorded as the response model id.
    """

    backend = "gemini"

    def __init__(self, model_id: str | None = None, *, api_key: str | None = None, base_url: str | None = None,
                 timeout_s: float | None = None, opener: Callable[..., Any] | None = None,
                 env: dict[str, str] | None = None):
        s = provider_config.settings(env)
        model = model_id or s.model_id
        problem = provider_config.model_id_problem(model)
        if problem:
            raise ProviderConfigError(f"Gemini model: {problem}")
        key = api_key or provider_config.api_key(env)
        if not key:
            raise ProviderConfigError(f"{provider_config.API_KEY_ENV} is not set in the runtime environment: the "
                                      "Gemini evaluator cannot run (set it as a runtime variable; never commit it)")
        self.model_id = model
        self._key = key
        self.base_url = (base_url or s.base_url).rstrip("/")
        self.timeout_s = timeout_s if timeout_s is not None else s.timeout_s
        self._open = opener or urllib.request.urlopen

    def __repr__(self) -> str:  # never shows the key
        return f"GeminiClient(model_id={self.model_id!r})"

    @staticmethod
    def contents(messages: list[dict[str, str]]) -> tuple[list[dict[str, Any]], str | None]:
        system: list[str] = []
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "user")
            if role == "system":
                system.append(m["content"])
                continue
            g_role = "model" if role == "assistant" else "user"
            if out and out[-1]["role"] == g_role:
                out[-1]["parts"].append({"text": m["content"]})
            else:
                out.append({"role": g_role, "parts": [{"text": m["content"]}]})
        return out, ("\n\n".join(system) if system else None)

    def payload(self, request: LLMRequest) -> dict[str, Any]:
        contents, system = self.contents(request.messages)
        gen: dict[str, Any] = {"temperature": request.temperature, "candidateCount": 1,
                               "responseMimeType": "application/json"}
        if request.max_tokens:
            gen["maxOutputTokens"] = request.max_tokens
        seed = request.metadata.get("seed")
        if seed is not None:
            gen["seed"] = seed
        body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body

    def complete(self, request: LLMRequest, attempt: int) -> LLMResponse:
        try:
            data = _post_json(self._open, f"{self.base_url}/models/{self.model_id}:generateContent",
                              self.payload(request), {"x-goog-api-key": self._key}, self.timeout_s,
                              secrets=(self._key,))
        except ProviderRequestError as exc:
            if exc.status == 404:  # never fall back to another model: the pinned model is the experiment (P-3)
                raise ProviderRequestError(
                    f"the configured Gemini model {self.model_id!r} is unavailable to this key (HTTP 404). Set "
                    f"{provider_config.MODEL_ENV} to a model this key can use; no other model is tried. {exc}",
                    404) from None
            raise
        cands = data.get("candidates") or []
        content, stop = "", "STOP"
        if not isinstance(cands, list) or not cands:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            stop = f"BLOCKED:{block}" if block else "NO_CANDIDATES"
        else:
            cand = cands[0] if isinstance(cands[0], dict) else {}
            parts = (cand.get("content") or {}).get("parts") or []
            content = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and not p.get("thought"))
            stop = str(cand.get("finishReason") or "STOP")
        usage = data.get("usageMetadata") or {}
        thoughts = int(usage.get("thoughtsTokenCount") or 0)
        return LLMResponse(request.request_id, str(data.get("modelVersion") or self.model_id), content,
                           input_tokens=int(usage.get("promptTokenCount") or 0),
                           output_tokens=int(usage.get("candidatesTokenCount") or 0) + thoughts,
                           cached_tokens=int(usage.get("cachedContentTokenCount") or 0), stop_reason=stop,
                           reasoning_tokens=thoughts)
