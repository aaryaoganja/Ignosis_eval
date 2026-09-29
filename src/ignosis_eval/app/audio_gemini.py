"""EXPERIMENTAL audio-only evaluation for the review app: a Gemini speech-to-text model as the ASR adapter.

Owner-authorized prototype path (2026-09-29, spec-reconciliation §3.50). It is NOT the B-06 ASR / diarization
decision and is not calibrated against the B-11 thresholds. It lives in the app package only: the runner, the
benchmark, gold derivation and the reliability metrics never import it (tests/test_architecture_boundaries.py), so
an experimental audio result can never enter a benchmark run, gold or a reliability number.

    audio bytes -> GEMINI_TRANSCRIBE_MODEL (speech-to-text, speaker diarization, word timestamps)
                -> diarized turns -> outbound-call role rule (R-02) -> ASRResult (the existing adapter contract)
                -> normalize_audio_result -> the shared front end -> Evaluator B on GEMINI_MODEL (extraction,
                   judgments, rule engine) -> evaluation record -> result view

- **Two models, two jobs.** The transcription model only transcribes and separates speakers; it never judges. The
  evaluator model never hears the audio; it judges the turns exactly as it judges a transcript.
- **Roles are named by rule, never guessed (R-02: no LLM role fallback).** Diarization labels speakers anonymously
  (spk_1, spk_2). With exactly two diarized speakers, the one who opens the call and also speaks the most words is the
  AGENT (an outbound collections call: the agent places it and delivers the opening) and the other is the BORROWER.
  No speaker labels, one speaker, three or more, or the two criteria disagreeing: no role is assigned, every turn is
  UNKNOWN and the call is NOT EVALUABLE (ROLE_UNCERTAIN). The rule is categorical; no confidence score is invented.
- **Timing** comes from the model's word timestamps when every turn has them; otherwise the unit has none.
- **Response format.** Speaker labels and offsets are read from word entries wherever they appear in the response
  (camelCase or snake_case; `word_info`-style annotations or word lists), or from `spk_N:` line prefixes. If none
  are present the result is NOT EVALUABLE with that reason, and only the response's key names are logged.
- **Privacy.** The recording is sent inline (no Files API upload) and without its file name (P-17); the app keeps
  only its sha256.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from ignosis_eval.contracts.enums import Role
from ignosis_eval.evaluators import provider_config
from ignosis_eval.evaluators.base import TraceSink
from ignosis_eval.evaluators.llm import (
    GeminiClient,
    LLMRequest,
    LLMResponse,
    OutputSchemaError,
    ProviderConfigError,
    ProviderRequestError,
    RecordingClient,
    _post_json,
    structured_call,
)
from ignosis_eval.pipeline.asr import ASRAdapter, ASRResult, ASRTurn
from ignosis_eval.pipeline.intake import parse_txt

log = logging.getLogger("ignosis_eval.app")

ENGINE = "gemini-transcribe-diarization-experimental"
PIPELINE_VERSION = "audio-transcribe/0.2-experimental"
REPLAY_ENGINE = "demo-replay-audio-script"
LABEL = "EXPERIMENTAL AUDIO EVALUATION"
CAVEAT = "Audio transcription and speaker attribution are not included in final reliability claims."
RELIABILITY = "Not independently calibrated"
MAX_TURNS = 400
TRANSCRIPTION_CONFIG: dict[str, Any] = {"diarization": True, "wordTimestamp": True}  # verbatim mode (the default)
ROLE_RULE = ("outbound-call rule (R-02): with exactly two diarized speakers, the one who opens the call and also "
             "speaks the most words is the agent and the other the borrower; otherwise no role is assigned")


class AudioInputError(ValueError):
    """The upload is not an audio file this path can send (code + reviewer-facing message)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class AudioInterpretationError(RuntimeError):
    """The transcription model answered, but not with a usable transcription (twice), or declined the audio."""


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


# ------------------------------------------------------------------------------------------ transcription parsing
_SPEAKER_KEYS = ("speaker", "speakerLabel", "speaker_label", "speakerTag", "speaker_tag", "speakerId", "speaker_id")
_START_KEYS = ("startOffset", "start_offset", "startTime", "start_time")
_END_KEYS = ("endOffset", "end_offset", "endTime", "end_time")
_LANG_KEYS = ("languageCode", "language_code")
_PREFIX = re.compile(r"^\s*\[?(spk_?\d+|speaker[ _]?\d+)\]?\s*[:\-]\s*(.+)$", re.IGNORECASE)


@dataclass
class Word:
    text: str
    speaker: str | None
    start: float | None = None
    end: float | None = None
    confidence: float | None = None


@dataclass
class Transcription:
    words: list[Word]
    text: str
    language: str | None
    response_keys: list[str] = field(default_factory=list)


def _seconds(v: Any) -> float | None:
    """'1.250s', '1.25', 1.25 or {'seconds': 1, 'nanos': 250000000} -> 1.25; anything else -> None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v >= 0 else None
    if isinstance(v, str):
        try:
            x = float(v.strip().rstrip("s"))
        except ValueError:
            return None
        return x if x >= 0 else None
    if isinstance(v, dict) and ("seconds" in v or "nanos" in v):
        return float(v.get("seconds") or 0) + float(v.get("nanos") or 0) / 1e9
    return None


def _first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((d[k] for k in keys if k in d and d[k] not in (None, "")), None)


def _word(d: dict[str, Any]) -> Word | None:
    """A word (or utterance) entry: text plus a speaker label or a time offset."""
    text = d.get("text") if isinstance(d.get("text"), str) else d.get("word")
    if not isinstance(text, str) or not text.strip():
        return None
    spk, start = _first(d, _SPEAKER_KEYS), _first(d, _START_KEYS)
    if spk is None and start is None:
        return None
    conf = d.get("confidence")
    return Word(text.strip(), str(spk).strip().lower() if spk is not None else None, _seconds(start),
                _seconds(_first(d, _END_KEYS)), float(conf) if isinstance(conf, (int, float)) else None)


def _collect(node: Any, words: list[Word], langs: list[str]) -> None:
    if isinstance(node, dict):
        w = _word(node)
        if w is not None:
            words.append(w)
            return
        for k, v in node.items():
            if k in _LANG_KEYS and isinstance(v, str) and 0 < len(v) <= 20:
                langs.append(v)
            _collect(v, words, langs)
    elif isinstance(node, list):
        for x in node:
            _collect(x, words, langs)


def parse_transcription(raw: str) -> Transcription:
    """The transcription model's generateContent response (kept whole by GeminiTranscribeClient)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OutputSchemaError(f"transcription response is not JSON: {exc}") from None
    cands = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(cands, list) or not cands or not isinstance(cands[0], dict):
        block = ((data.get("promptFeedback") or {}).get("blockReason") if isinstance(data, dict) else None)
        raise OutputSchemaError(f"no transcription candidate{f' (blocked: {block})' if block else ''}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and not p.get("thought"))
    words: list[Word] = []
    langs: list[str] = []
    _collect(data, words, langs)
    if not any(w.speaker for w in words):  # no labelled words: speaker-prefixed lines, if the text has them
        prefixed = [(m.group(1), m.group(2)) for m in map(_PREFIX.match, text.splitlines()) if m]
        if prefixed:
            words = [Word(t.strip(), re.sub(r"[ _]", "_", spk.lower()).replace("speaker", "spk"))
                     for spk, t in prefixed]
    if not words and text.strip():
        words = [Word(" ".join(text.split()), None)]  # a transcript without speaker labels
    return Transcription(words, text, langs[0] if langs else None, sorted(data))


@dataclass
class Segment:
    speaker: str | None
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(" ".join(w.text for w in self.words).split())

    @property
    def start(self) -> float | None:
        return self.words[0].start

    @property
    def end(self) -> float | None:
        return self.words[-1].end


def segments(words: list[Word]) -> list[Segment]:
    """Consecutive words of the same speaker form one turn."""
    out: list[Segment] = []
    for w in words:
        if out and out[-1].speaker == w.speaker:
            out[-1].words.append(w)
        else:
            out.append(Segment(w.speaker, [w]))
    return out


def assign_roles(segs: list[Segment]) -> tuple[dict[str, Role], str, str]:
    """The outbound-call rule (R-02). Returns (speaker -> role, status, explanation). Status: CLEAR, PARTIAL (roles
    assigned, some turns carry no speaker label), NO_SPEECH, NO_DIARIZATION, SPEAKER_COUNT, CONFLICT."""
    if not segs:
        return {}, "NO_SPEECH", "no speech was transcribed"
    labels = [s.speaker for s in segs if s.speaker]
    if not labels:
        return {}, "NO_DIARIZATION", "the transcription carried no speaker labels"
    distinct = list(dict.fromkeys(labels))
    if len(distinct) != 2:
        return {}, "SPEAKER_COUNT", f"{len(distinct)} speaker{'s' if len(distinct) != 1 else ''} were separated; " \
                                    "the rule needs exactly two"
    counts = {spk: sum(len(s.text.split()) for s in segs if s.speaker == spk) for spk in distinct}
    first = labels[0]
    most = max(distinct, key=lambda k: counts[k])
    if counts[distinct[0]] == counts[distinct[1]] or first != most:
        return {}, "CONFLICT", (f"{first} opens the call but {most} speaks the most words "
                                f"({counts[first]} vs {counts[most]})" if first != most else
                                "both speakers speak the same number of words")
    other = distinct[1] if first == distinct[0] else distinct[0]
    roles = {first: Role.AGENT, other: Role.BORROWER}
    status = "PARTIAL" if any(s.speaker is None for s in segs) else "CLEAR"
    return roles, status, (f"{first} opens the call and speaks the most words ({counts[first]} vs {counts[other]}): "
                           f"{first} = agent, {other} = borrower")


def to_result(tr: Transcription, adapter: ASRAdapter) -> tuple[ASRResult, dict[str, Any]]:
    segs = segments(tr.words)
    if len(segs) > MAX_TURNS:
        raise AudioInterpretationError(f"the transcription has more than {MAX_TURNS} turns")
    roles, status, detail = assign_roles(segs)
    clear = status in ("CLEAR", "PARTIAL")
    timed = all(s.start is not None and s.end is not None and s.start <= s.end for s in segs) and bool(segs)
    turns = tuple(ASRTurn(roles.get(s.speaker or "", Role.UNKNOWN) if clear else Role.UNKNOWN, s.text,
                          s.start if timed else None, s.end if timed else None, None,
                          tuple(w.confidence for w in s.words if w.confidence is not None) or None)
                  for s in segs)
    info = {"speech_detected": bool(segs), "language": tr.language, "role_status": status, "role_detail": detail,
            "role_separation": "CLEAR" if clear else "UNCERTAIN",
            "speakers": {k: v.value for k, v in roles.items()}, "diarized": any(w.speaker for w in tr.words),
            "turns": len(turns), "unknown_role_turns": sum(t.role is Role.UNKNOWN for t in turns),
            "unclear_turns": 0, "words": len(tr.words), "timestamps": timed}
    return ASRResult(turns, 1.0 if clear else 0.0, adapter.engine, adapter.model, adapter.version,
                     adapter.params_sha256), info


# ------------------------------------------------------------------------------------------ Gemini
class GeminiTranscribeClient(GeminiClient):
    """generateContent on GEMINI_TRANSCRIBE_MODEL with the recording inline and audioTranscriptionConfig (speaker
    diarization, word timestamps). Key handling and redaction are GeminiClient's; the whole response is kept (the
    speaker labels and offsets are not in the text parts). No fallback: an unavailable model is a readable error."""

    def __init__(self, audio: bytes, mime_type: str, **kw: Any):
        model = kw.pop("model_id", None) or provider_config.settings().transcribe_model_id
        problem = provider_config.transcribe_model_problem(model)
        if problem:
            raise ProviderConfigError(f"Transcription model ({provider_config.TRANSCRIBE_MODEL_ENV}): {problem}")
        kw.setdefault("timeout_s", provider_config.AUDIO_TIMEOUT_S)
        super().__init__(model_id=model, **kw)
        self._audio_b64 = base64.b64encode(audio).decode("ascii")
        self._mime = mime_type

    def __repr__(self) -> str:  # never shows the key or the audio
        return f"GeminiTranscribeClient(model_id={self.model_id!r}, mime={self._mime!r})"

    def payload(self, request: LLMRequest) -> dict[str, Any]:
        return {"contents": [{"role": "user", "parts": [{"inlineData": {"mimeType": self._mime,
                                                                          "data": self._audio_b64}}]}],
                "generationConfig": {"audioTranscriptionConfig": dict(TRANSCRIPTION_CONFIG)}}

    def complete(self, request: LLMRequest, attempt: int) -> LLMResponse:
        try:
            data = _post_json(self._open, f"{self.base_url}/models/{self.model_id}:generateContent",
                              self.payload(request), {"x-goog-api-key": self._key}, self.timeout_s,
                              secrets=(self._key,))
        except ProviderRequestError as exc:
            if exc.status == 404:
                raise ProviderRequestError(
                    f"the configured transcription model {self.model_id!r} is unavailable to this key (HTTP 404). "
                    f"Set {provider_config.TRANSCRIBE_MODEL_ENV} to a model this key can use; no other model is "
                    f"tried. {exc}", 404) from None
            raise
        usage = data.get("usageMetadata") or {}
        return LLMResponse(request.request_id, str(data.get("modelVersion") or self.model_id), json.dumps(data),
                           input_tokens=int(usage.get("promptTokenCount") or 0),
                           output_tokens=int(usage.get("candidatesTokenCount") or 0))


class GeminiTranscriber(ASRAdapter):
    """The experimental adapter. `interpret` works on bytes in memory (the app never writes an upload to disk);
    `transcribe` is the ASRAdapter entry point for a file."""

    def __init__(self, *, trace: TraceSink | None = None, sleep: Callable[[float], None] | None = None,
                 client_kw: dict[str, Any] | None = None):
        super().__init__(engine=ENGINE, version=PIPELINE_VERSION, model=provider_config.settings().transcribe_model_id,
                         params={"transcription_config": TRANSCRIPTION_CONFIG, "role_rule": "R-02 outbound: opens "
                                 "the call and speaks most = agent; exactly two speakers", "pipeline": PIPELINE_VERSION})
        self.trace = trace or TraceSink()
        self._sleep = sleep or time.sleep
        self._client_kw = client_kw or {}

    def transcribe(self, audio_path: Path, audio_sha256: str) -> ASRResult:
        return self.interpret(audio_path.read_bytes(), audio_path.suffix.lower().lstrip("."))[0]

    def interpret(self, data: bytes, fmt: str) -> tuple[ASRResult, dict[str, Any]]:
        mime = provider_config.AUDIO_MIME_TYPES.get(fmt)
        if mime is None:
            raise AudioInputError("AUDIO_FORMAT", f"Audio-only accepts {', '.join(sorted(provider_config.AUDIO_MIME_TYPES))}.")
        client = GeminiTranscribeClient(data, mime, **self._client_kw)  # ProviderConfigError before any call
        s = provider_config.settings()
        req = LLMRequest(f"transcribe-{uuid.uuid4().hex[:10]}", "audio_transcribe", client.model_id, s.temperature,
                         None, [], {})
        rc = RecordingClient(client, s.transport, self.trace, self._sleep)
        tr, _raw, error, _attempts = structured_call(rc, req, parse_transcription)
        if tr is None:
            why = (f"the transcription model declined to process the recording ({error})" if error and "blocked" in error
                   else f"the transcription response was not usable twice: {error}")
            raise AudioInterpretationError(why)
        if not any(w.speaker for w in tr.words):
            log.warning("transcription response carried no speaker labels; response keys: %s", tr.response_keys)
        result, info = to_result(tr, self)
        served = next((str(r.get("model_id")) for r in reversed(self.trace.responses) if r.get("ok")), client.model_id)
        return result, {**info, "served_model": served}


class _ReplayAdapter(ASRAdapter):
    def transcribe(self, audio_path: Path, audio_sha256: str) -> ASRResult:  # pragma: no cover - not used
        raise NotImplementedError


def replay_interpretation(script: str) -> tuple[ASRResult, dict[str, Any]]:
    """DEMO / REPLAY for the app's sample recording: its own fictional script (the text it was synthesised from)
    stands in for the transcription. No model is called; the result is labelled as a replay."""
    adapter = _ReplayAdapter(engine=REPLAY_ENGINE, version=PIPELINE_VERSION, model=None, params={"replay": True})
    turns = tuple(ASRTurn(t.role, " ".join(t.text.split()), None, None, None) for t in parse_txt(script).turns)
    info: dict[str, Any] = {"speech_detected": True, "language": None, "role_status": "REPLAY",
            "role_detail": "the recording's own script supplies the speaker roles; no model listened to it",
            "role_separation": "CLEAR", "speakers": {}, "diarized": False, "turns": len(turns),
            "unknown_role_turns": 0, "unclear_turns": 0, "words": sum(len(t.text.split()) for t in turns),
            "timestamps": False, "served_model": None}
    return ASRResult(turns, 1.0, adapter.engine, adapter.model, adapter.version, adapter.params_sha256), info


__all__ = ["CAVEAT", "ENGINE", "LABEL", "PIPELINE_VERSION", "RELIABILITY", "ROLE_RULE", "TRANSCRIPTION_CONFIG",
           "AudioInputError", "AudioInterpretationError", "GeminiTranscribeClient", "GeminiTranscriber", "Segment",
           "Transcription", "Word", "assign_roles", "check_audio", "parse_transcription", "replay_interpretation",
           "segments", "sniff_format", "to_result"]
