"""EXPERIMENTAL audio-only evaluation for the review app: Gemini's native audio understanding as an ASR adapter.

Owner-authorized prototype path (2026-09-29, spec-reconciliation §3.50). It is NOT the B-06 ASR / diarization
decision and is not calibrated against the B-11 thresholds. It lives in the app package only: the runner, the
benchmark, gold derivation and the reliability metrics never import it (tests/test_architecture_boundaries.py), so
an experimental audio result can never enter a benchmark run, gold or a reliability number.

    audio bytes -> Gemini (inline audio + JSON-schema-constrained output) -> turns with speaker roles
                -> ASRResult (the existing adapter contract) -> normalize_audio_result -> the shared front end
                -> Evaluator B (extraction, judgments, rule engine) -> evaluation record -> result view

- **One data model.** Gemini returns only what an ASR / diarization step returns: ordered turns, a speaker role per
  turn and whether a turn was heard clearly. Events, evidence spans and verdicts come from the existing Evaluator B
  over those turns, exactly as for a transcript.
- **Speaker uncertainty is preserved, never forced.** A turn whose speaker Gemini cannot tell is UNKNOWN, which the
  front end marks span-unreliable (checks citing it become INCONCLUSIVE). If Gemini reports that it cannot tell the
  agent from the borrower at all (role_separation UNCERTAIN), the call-level role gate fails and the call is NOT
  EVALUABLE (ROLE_UNCERTAIN). CLEAR / UNCERTAIN is Gemini's own categorical report, mapped to the gate value 1.0 /
  0.0: it is not a measured confidence and is never shown as a score.
- **No timing.** Gemini's timestamps are approximate, so none are requested: the unit has no timestamps and timing
  signals (response gaps, overlap, monologue duration) are not measured; TRT-06 falls back to word count.
- **Privacy.** The recording is sent inline (no Files API upload, so nothing is stored at the provider for later
  requests) and without its file name (P-17); the app keeps only its sha256.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
import wave
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ignosis_eval.canonical import canonical_sha256
from ignosis_eval.contracts.enums import Role
from ignosis_eval.evaluators import provider_config
from ignosis_eval.evaluators.base import TraceSink
from ignosis_eval.evaluators.llm import (
    GeminiClient,
    LLMRequest,
    OutputSchemaError,
    RecordingClient,
    structured_call,
)
from ignosis_eval.pipeline.asr import ASRAdapter, ASRResult, ASRTurn
from ignosis_eval.pipeline.intake import parse_txt

ENGINE = "gemini-audio-understanding-experimental"
PROMPT_VERSION = "audio-interpretation/0.1-experimental"
REPLAY_ENGINE = "demo-replay-audio-script"
LABEL = "EXPERIMENTAL AUDIO EVALUATION"
CAVEAT = "Audio transcription/speaker attribution has not been independently calibrated for this prototype."
RELIABILITY = "Not independently calibrated"
MAX_TURNS = 400

SYSTEM = (
    "You transcribe recorded phone calls for a quality review. The call is between a debt-collection voice agent "
    "(the AGENT, calling on behalf of a lender) and a customer (the BORROWER); other people or automated systems may "
    "also speak (OTHER).\n"
    "Rules:\n"
    "1. Transcribe every spoken turn in the order spoken, verbatim, in the language spoken (English, Hindi or "
    "Hinglish; write Hindi in Latin script as spoken). Do not summarise, correct, translate or judge anything.\n"
    "2. For each turn give speaker_role: AGENT, BORROWER, OTHER, or UNKNOWN when you cannot tell who is speaking. "
    "Never guess a role: use UNKNOWN.\n"
    "3. Set unclear=true for a turn if any words could not be made out with confidence, and write [inaudible] "
    "for them. Never fill gaps with guesses.\n"
    "4. Set role_separation to CLEAR only if you can tell the agent's voice from the borrower's throughout the "
    "call; otherwise UNCERTAIN. Explain briefly in role_separation_note.\n"
    "5. Set speech_detected=false if the recording has no intelligible conversation (silence, noise, tones only).")
INSTRUCTION = "Transcribe the attached call recording following the rules. Return only the JSON object."

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "speech_detected": {"type": "BOOLEAN"},
        "language": {"type": "STRING", "description": "main language(s) spoken"},
        "role_separation": {"type": "STRING", "enum": ["CLEAR", "UNCERTAIN"]},
        "role_separation_note": {"type": "STRING"},
        "turns": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"speaker_role": {"type": "STRING", "enum": ["AGENT", "BORROWER", "OTHER", "UNKNOWN"]},
                           "text": {"type": "STRING"},
                           "unclear": {"type": "BOOLEAN"}},
            "required": ["speaker_role", "text", "unclear"],
            "propertyOrdering": ["speaker_role", "text", "unclear"]}},
    },
    "required": ["speech_detected", "language", "role_separation", "role_separation_note", "turns"],
    "propertyOrdering": ["speech_detected", "language", "role_separation", "role_separation_note", "turns"],
}


class AudioInputError(ValueError):
    """The upload is not an audio file this path can send (code + reviewer-facing message)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class AudioInterpretationError(RuntimeError):
    """Gemini answered, but not with a usable interpretation (schema-invalid twice, blocked or empty)."""


class _Turn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    speaker_role: Literal["AGENT", "BORROWER", "OTHER", "UNKNOWN"]
    text: str
    unclear: bool = False


class AudioInterpretation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    speech_detected: bool
    language: str = ""
    role_separation: Literal["CLEAR", "UNCERTAIN"]
    role_separation_note: str = ""
    turns: list[_Turn]


def parse_interpretation(raw: str) -> AudioInterpretation:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OutputSchemaError(f"audio interpretation is not JSON: {exc}") from None
    try:
        interp = AudioInterpretation.model_validate(data)
    except ValidationError as exc:
        raise OutputSchemaError(f"audio interpretation does not match the schema: {str(exc)[:500]}") from None
    if len(interp.turns) > MAX_TURNS:
        raise OutputSchemaError(f"more than {MAX_TURNS} turns")
    return interp


# ------------------------------------------------------------------------------------------ upload checks
def sniff_format(data: bytes) -> str | None:
    """The container format from the file's first bytes (never trust the extension alone)."""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "m4a"
    return None


def check_audio(data: bytes, ext: str) -> float | None:
    """Raise AudioInputError when the bytes are not a readable `ext` recording; return the WAV duration (s) when it
    can be read from the header (PCM), else None."""
    names = {"wav": ".wav", "mp3": ".mp3", "m4a": ".m4a"}
    if sniff_format(data) != ext:
        raise AudioInputError("AUDIO_MALFORMED", f"The file does not look like a valid {names.get(ext, ext)} "
                                                 "recording (its contents do not match the extension).")
    if ext != "wav":
        return None
    try:
        with wave.open(BytesIO(data), "rb") as w:
            frames, rate = w.getnframes(), w.getframerate()
    except wave.Error as exc:
        if "unknown format" in str(exc):  # e.g. mu-law telephone WAV: valid, just not PCM
            return None
        raise AudioInputError("AUDIO_MALFORMED", "The .wav file is damaged or incomplete.") from None
    except (EOFError, ValueError, OSError):
        raise AudioInputError("AUDIO_MALFORMED", "The .wav file is damaged or incomplete.") from None
    if frames == 0 or rate <= 0:
        raise AudioInputError("AUDIO_EMPTY", "The recording contains no audio.")
    return round(frames / rate, 1)


# ------------------------------------------------------------------------------------------ Gemini
class GeminiAudioClient(GeminiClient):
    """GeminiClient plus the recording as an inline part and a JSON response schema. Key handling, model
    validation (no fallback on 404), redaction and response parsing are the parent's."""

    def __init__(self, audio: bytes, mime_type: str, **kw: Any):
        kw.setdefault("timeout_s", provider_config.AUDIO_TIMEOUT_S)
        super().__init__(**kw)
        self._audio_b64 = base64.b64encode(audio).decode("ascii")
        self._mime = mime_type

    def __repr__(self) -> str:  # never shows the key or the audio
        return f"GeminiAudioClient(model_id={self.model_id!r}, mime={self._mime!r})"

    def payload(self, request: LLMRequest) -> dict[str, Any]:
        body = super().payload(request)
        body["contents"][0]["parts"].insert(0, {"inlineData": {"mimeType": self._mime, "data": self._audio_b64}})
        body["generationConfig"]["responseSchema"] = RESPONSE_SCHEMA
        return body


class GeminiAudioInterpreter(ASRAdapter):
    """The experimental adapter. `interpret` works on bytes in memory (the app never writes an upload to disk);
    `transcribe` is the ASRAdapter entry point for a file."""

    def __init__(self, *, trace: TraceSink | None = None, sleep: Callable[[float], None] | None = None,
                 client_kw: dict[str, Any] | None = None):
        s = provider_config.settings()
        super().__init__(engine=ENGINE, version=PROMPT_VERSION, model=s.model_id,
                         params={"prompt": PROMPT_VERSION, "schema_sha256": canonical_sha256(RESPONSE_SCHEMA),
                                 "temperature": s.temperature, "seed": s.seed, "timing": "none"})
        self.trace = trace or TraceSink()
        self._sleep = sleep or time.sleep
        self._client_kw = client_kw or {}

    def transcribe(self, audio_path: Path, audio_sha256: str) -> ASRResult:
        return self.interpret(audio_path.read_bytes(), audio_path.suffix.lower().lstrip("."))[0]

    def interpret(self, data: bytes, fmt: str) -> tuple[ASRResult, dict[str, Any]]:
        mime = provider_config.AUDIO_MIME_TYPES.get(fmt)
        if mime is None:
            raise AudioInputError("AUDIO_FORMAT", f"Audio-only accepts {', '.join(sorted(provider_config.AUDIO_MIME_TYPES))}.")
        client = GeminiAudioClient(data, mime, **self._client_kw)  # ProviderConfigError before any call
        s = provider_config.settings()
        req = LLMRequest(f"audio-{uuid.uuid4().hex[:10]}", "audio_interpret", client.model_id, s.temperature,
                         s.max_output_tokens, [{"role": "system", "content": SYSTEM},
                                               {"role": "user", "content": INSTRUCTION}], {"seed": s.seed})
        rc = RecordingClient(client, s.transport, self.trace, self._sleep)
        interp, _raw, error, _attempts = structured_call(rc, req, parse_interpretation)
        if interp is None:
            last = next((r for r in reversed(self.trace.responses) if r.get("ok")), {})
            stop = str(last.get("stop_reason") or "")
            why = (f"Gemini declined to process the recording ({stop})" if stop.startswith("BLOCKED")
                   else f"Gemini's audio interpretation was not usable twice: {error}")
            raise AudioInterpretationError(why)
        served = next((str(r.get("model_id")) for r in reversed(self.trace.responses) if r.get("ok")), client.model_id)
        return to_result(interp, self), {**summary(interp), "served_model": served}


def to_result(interp: AudioInterpretation, adapter: ASRAdapter) -> ASRResult:
    """Turns for the shared front end. When Gemini cannot separate the speakers, no role is kept (every turn becomes
    UNKNOWN) and the call-level mapping value is 0.0, so the DC-00 role gate makes the call NOT EVALUABLE."""
    clear = interp.speech_detected and interp.role_separation == "CLEAR"
    turns = tuple(ASRTurn(Role(t.speaker_role) if clear else Role.UNKNOWN, " ".join(t.text.split()), None, None,
                          None, None, unreliable=t.unclear) for t in interp.turns if t.text.strip())
    return ASRResult(turns, 1.0 if clear else 0.0, adapter.engine, adapter.model, adapter.version,
                     adapter.params_sha256)


def summary(interp: AudioInterpretation) -> dict[str, Any]:
    kept = [t for t in interp.turns if t.text.strip()]
    return {"speech_detected": interp.speech_detected, "language": interp.language[:60],
            "role_separation": interp.role_separation if interp.speech_detected else "UNCERTAIN",
            "role_separation_note": " ".join(interp.role_separation_note.split())[:400],
            "turns": len(kept), "unknown_role_turns": sum(t.speaker_role == "UNKNOWN" for t in kept),
            "unclear_turns": sum(t.unclear for t in kept)}


class _ReplayAdapter(ASRAdapter):
    def transcribe(self, audio_path: Path, audio_sha256: str) -> ASRResult:  # pragma: no cover - not used
        raise NotImplementedError


def replay_interpretation(script: str) -> tuple[ASRResult, dict[str, Any]]:
    """DEMO / REPLAY for the app's sample recording: its own fictional script (the text it was synthesised from)
    stands in for Gemini's interpretation. No model is called; the result is labelled as a replay."""
    adapter = _ReplayAdapter(engine=REPLAY_ENGINE, version=PROMPT_VERSION, model=None, params={"replay": True})
    parsed = parse_txt(script)
    interp = AudioInterpretation(
        speech_detected=True, language="English", role_separation="CLEAR",
        role_separation_note="",
        turns=[_Turn(speaker_role=t.role.value, text=t.text) for t in parsed.turns])  # type: ignore[arg-type]
    return to_result(interp, adapter), {**summary(interp), "served_model": None}


__all__ = ["CAVEAT", "ENGINE", "LABEL", "PROMPT_VERSION", "RELIABILITY", "RESPONSE_SCHEMA", "AudioInputError",
           "AudioInterpretation", "AudioInterpretationError", "GeminiAudioClient", "GeminiAudioInterpreter",
           "check_audio", "parse_interpretation", "replay_interpretation", "sniff_format"]
