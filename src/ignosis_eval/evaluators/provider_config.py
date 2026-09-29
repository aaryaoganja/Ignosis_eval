"""Evaluator provider configuration: the ONE place where the LLM provider, the model id and the call settings live.

Provider: Google Gemini, a non-Claude family (authoring constraint 1: the DEV drafts are Claude-assisted). The
owner chose it for DEV engineering runs on 2026-09-29. The spec's B-05 pin for locked runs stays
PENDING_HUMAN_SIGNOFF (spec-reconciliation §3.48).

Secrets:
- The API key exists only as the runtime environment variable GEMINI_API_KEY.
- `api_key()` is its only reader in this package.
- The key is never written to a file, record, trace or log, and `describe()` never includes it.
- Nothing needs the key at build or import time.

Non-secret overrides (runtime env, optional):
- GEMINI_MODEL: the evaluator model (reasoning, extraction, judgments). A pinned model id, validated: no "latest" /
  "preview" / "exp" aliases (P-3).
- GEMINI_TRANSCRIBE_MODEL: the speech-to-text model used ONLY by the review app's EXPERIMENTAL audio-only path
  (transcription with speaker diarization and word timestamps). Same validation, and never a Live (streaming) model.
- GEMINI_BASE_URL: the API root, for a proxy or a test double.
Neither model falls back to another: an unavailable model fails the call with a readable error and no verdict.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ignosis_eval.contracts.run_manifest import TransportRetryPolicy

PROVIDER = "gemini"
PROVIDER_LABEL = "Google Gemini API (generateContent, REST)"
FAMILY = "google-gemini"
API_KEY_ENV = "GEMINI_API_KEY"
MODEL_ENV = "GEMINI_MODEL"
TRANSCRIBE_MODEL_ENV = "GEMINI_TRANSCRIBE_MODEL"
BASE_URL_ENV = "GEMINI_BASE_URL"
DEFAULT_MODEL = "gemini-3.8-flash"  # stable (GA) model code; the served version is recorded per response
DEFAULT_TRANSCRIBE_MODEL = "gemini-3.5-transcribe"  # file-based speech-to-text (diarization, word timestamps)
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
TEMPERATURE = 0.0  # P-3
SEED = 0  # P-3: set when the API supports it (Gemini generationConfig.seed); recorded
TIMEOUT_S = 120.0
MAX_OUTPUT_TOKENS: int | None = None  # provider default; recorded as null
SCHEMA_RETRIES = 1  # P-3
STRUCTURED_OUTPUT = "JSON mode (responseMimeType application/json) + contract validation, 1 schema retry"
INPUT_MODALITY = "TRANSCRIPT"  # text only; audio reaches the evaluator as a transcript (ASR is B-06)

# EXPERIMENTAL audio-only path (owner-authorized prototype, 2026-09-29; review app only, spec-reconciliation §3.50).
# GEMINI_TRANSCRIBE_MODEL transcribes an audio-only upload with speaker diarization and word timestamps; a
# deterministic outbound-call rule names the two speakers (R-02); the existing front end and Evaluator B (GEMINI_MODEL)
# then judge the turns. It is NOT the B-06 ASR/diarization decision, is not calibrated against the B-11 thresholds,
# and never feeds a benchmark run, gold or a reliability metric.
AUDIO_UNDERSTANDING = ("EXPERIMENTAL (review app only): transcription model with diarization, inline audio; not "
                       "calibrated (B-06/B-11)")
AUDIO_MIME_TYPES = {"wav": "audio/wav", "mp3": "audio/mp3"}  # app formats that Gemini documents for audio input
AUDIO_MAX_INLINE_BYTES = 14 * 1024 * 1024  # base64 keeps the whole request under Gemini's 20 MB inline limit
AUDIO_TIMEOUT_S = 180.0

_MODEL_ID = re.compile(r"^gemini-[0-9][0-9a-z.\-]*$")
_ALIAS_TOKENS = {"latest", "preview", "exp", "experimental"}


def model_id_problem(model_id: str | None) -> str | None:
    """Why `model_id` is not a pinned Gemini model code, or None when it is."""
    if not model_id:
        return "no model id"
    tokens = set(model_id.lower().split("-"))
    if tokens & _ALIAS_TOKENS or any(t.startswith("exp") for t in tokens):
        return f"{model_id!r} is an alias or pre-release model: P-3 forbids aliases like 'latest'"
    if not _MODEL_ID.match(model_id):
        return f"{model_id!r} is not a Gemini model code (expected gemini-<version>-<variant>)"
    return None


def transcribe_model_problem(model_id: str | None) -> str | None:
    """Why `model_id` cannot be the transcription model, or None: a pinned Gemini model code, not a Live model."""
    problem = model_id_problem(model_id)
    if problem:
        return problem
    if "live" in str(model_id).lower().split("-"):
        return f"{model_id!r} is a Live (streaming) model; offline call QA uses file transcription"
    return None


@dataclass(frozen=True)
class ProviderSettings:
    provider: str
    family: str
    model_id: str
    base_url: str
    temperature: float
    seed: int | None
    timeout_s: float
    max_output_tokens: int | None
    schema_retries: int
    input_modality: str
    transcribe_model_id: str = DEFAULT_TRANSCRIBE_MODEL
    transport: TransportRetryPolicy = field(default_factory=TransportRetryPolicy)


def settings(env: Mapping[str, str] | None = None) -> ProviderSettings:
    e = os.environ if env is None else env
    return ProviderSettings(
        provider=PROVIDER, family=FAMILY, model_id=(e.get(MODEL_ENV) or DEFAULT_MODEL).strip(),
        base_url=(e.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/"), temperature=TEMPERATURE, seed=SEED,
        timeout_s=TIMEOUT_S, max_output_tokens=MAX_OUTPUT_TOKENS, schema_retries=SCHEMA_RETRIES,
        input_modality=INPUT_MODALITY,
        transcribe_model_id=(e.get(TRANSCRIBE_MODEL_ENV) or DEFAULT_TRANSCRIBE_MODEL).strip())


def api_key(env: Mapping[str, str] | None = None) -> str | None:
    """The key, read at call time from the runtime environment only."""
    value = (os.environ if env is None else env).get(API_KEY_ENV, "").strip()
    return value or None


def api_key_configured(env: Mapping[str, str] | None = None) -> bool:
    return api_key(env) is not None


def describe(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The evaluator provider configuration, safe to show, log and return to a browser: it never includes the key."""
    s = settings(env)
    problem = model_id_problem(s.model_id)
    t_problem = transcribe_model_problem(s.transcribe_model_id)
    e = os.environ if env is None else env
    return {
        "provider": s.provider, "provider_label": PROVIDER_LABEL, "family": s.family, "model_id": s.model_id,
        "model_id_valid": problem is None, "model_id_problem": problem,
        "model_source": "env GEMINI_MODEL" if e.get(MODEL_ENV) else "default",
        "transcribe_model_id": s.transcribe_model_id, "transcribe_model_valid": t_problem is None,
        "transcribe_model_problem": t_problem,
        "transcribe_model_source": "env GEMINI_TRANSCRIBE_MODEL" if e.get(TRANSCRIBE_MODEL_ENV) else "default",
        "api_key_env": API_KEY_ENV, "api_key_configured": api_key_configured(env),
        "custom_base_url": s.base_url != DEFAULT_BASE_URL,
        "temperature": s.temperature, "seed": s.seed, "max_output_tokens": s.max_output_tokens,
        "timeout_s": s.timeout_s, "structured_output": STRUCTURED_OUTPUT, "schema_retries": s.schema_retries,
        "transport_retry": s.transport.model_dump(mode="json"), "input_modality": s.input_modality,
        "audio_understanding": AUDIO_UNDERSTANDING, "audio_formats": sorted(AUDIO_MIME_TYPES),
        "audio_max_mb": AUDIO_MAX_INLINE_BYTES // 2**20,
    }


__all__ = ["API_KEY_ENV", "AUDIO_MAX_INLINE_BYTES", "AUDIO_MIME_TYPES", "AUDIO_TIMEOUT_S", "AUDIO_UNDERSTANDING",
           "BASE_URL_ENV", "DEFAULT_MODEL", "DEFAULT_TRANSCRIBE_MODEL", "FAMILY", "MODEL_ENV", "PROVIDER",
           "ProviderSettings", "TRANSCRIBE_MODEL_ENV", "api_key", "api_key_configured", "describe", "model_id_problem",
           "settings", "transcribe_model_problem"]
