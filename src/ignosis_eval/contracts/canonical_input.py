"""Canonical Input contract: the only thing an evaluator is allowed to see about a call.

MVP input modes are exactly: audio only, transcript only, audio + transcript.
No CRM/LMS/account/payment data, no policy packs, no agent/tool traces.

Invariants (validated):
  * transcript_only   -> transcript present, audio absent, transcript not produced by pipeline ASR
  * audio_only        -> audio present; a transcript may be attached ONLY by the evaluation pipeline's
                         own ASR during normalization (provenance.source == pipeline_asr)
  * audio_transcript  -> both present
  * evidence_availability must agree with what is actually present
  * turns: unique turn_ids, index == position, start_ms <= end_ms, non-decreasing start times
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ignosis_eval.contracts._base import Contract, Id, LanguageTag, NonNegInt, Probability, Sha256Hex
from ignosis_eval.contracts.enums import (
    EvaluabilityIssue,
    InputMode,
    Speaker,
    TimestampSource,
    TranscriptSource,
)
from ignosis_eval.versions import CANONICAL_INPUT_SCHEMA

_ASR_SOURCES = {TranscriptSource.VENDOR_ASR, TranscriptSource.PIPELINE_ASR, TranscriptSource.HUMAN_CORRECTED_ASR}


class TranscriptProvenance(Contract):
    source: TranscriptSource
    asr_engine: str | None = None
    asr_model: str | None = None
    asr_version: str | None = None
    diarization: Literal["channel_separated", "model_diarized", "human", "scripted", "none"] = "none"
    timestamp_source: TimestampSource = TimestampSource.NONE
    language_detected: LanguageTag | None = None
    produced_at: AwareDatetime | None = None
    derived_from_audio_sha256: Sha256Hex | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _asr_fields(self) -> "TranscriptProvenance":
        if self.source in _ASR_SOURCES and not self.asr_engine:
            raise ValueError(f"transcript provenance source={self.source} requires asr_engine")
        if self.source is TranscriptSource.PIPELINE_ASR and not self.derived_from_audio_sha256:
            raise ValueError("pipeline_asr transcripts must record derived_from_audio_sha256")
        return self


class Turn(Contract):
    turn_id: Id
    index: NonNegInt
    speaker: Speaker
    text: str
    start_ms: NonNegInt | None = None
    end_ms: NonNegInt | None = None
    asr_confidence: Probability | None = None
    language: LanguageTag | None = None

    @model_validator(mode="after")
    def _times(self) -> "Turn":
        if self.start_ms is not None and self.end_ms is not None and self.start_ms > self.end_ms:
            raise ValueError(f"turn {self.turn_id}: start_ms > end_ms")
        return self

    @property
    def has_timestamps(self) -> bool:
        return self.start_ms is not None and self.end_ms is not None


class Transcript(Contract):
    provenance: TranscriptProvenance
    turns: list[Turn]

    @model_validator(mode="after")
    def _turns(self) -> "Transcript":
        seen: set[str] = set()
        last_start: int | None = None
        for pos, t in enumerate(self.turns):
            if t.turn_id in seen:
                raise ValueError(f"duplicate turn_id {t.turn_id!r}")
            seen.add(t.turn_id)
            if t.index != pos:
                raise ValueError(f"turn {t.turn_id}: index {t.index} != position {pos}")
            if t.start_ms is not None:
                if last_start is not None and t.start_ms < last_start:
                    raise ValueError(f"turn {t.turn_id}: start_ms decreases (turns must be in time order)")
                last_start = t.start_ms
        return self


class ChannelAssignment(Contract):
    channel: NonNegInt
    speaker: Speaker


class AudioRendering(Contract):
    """How the audio was produced. Required for synthetic audio; 'real_call' for recordings."""

    method: Literal["tts", "recorded_actor", "real_call", "unknown"]
    tts_engine: str | None = None
    tts_voice_ids: dict[str, str] | None = None  # speaker -> voice id
    noise_profile: str | None = None
    snr_db: float | None = None
    codec_simulation: str | None = None  # e.g. "g711_ulaw_8k"
    rendering_seed: int | None = None
    renderer_version: str | None = None
    source_script_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def _tts(self) -> "AudioRendering":
        if self.method == "tts" and not self.tts_engine:
            raise ValueError("audio rendering method=tts requires tts_engine")
        return self


class AudioMetadata(Contract):
    uri: str = Field(min_length=1, description="Path relative to the case directory (never an absolute path).")
    sha256: Sha256Hex
    format: Literal["wav", "mp3", "flac", "opus", "ogg", "m4a"]
    codec: str | None = None
    sample_rate_hz: int = Field(gt=0)
    channels: int = Field(ge=1)
    duration_ms: NonNegInt
    channel_map: list[ChannelAssignment] | None = None
    rendering: AudioRendering | None = None
    contains_real_pii: bool = Field(
        description="True if the audio is a real recording that may contain personal data. "
        "Such audio must never be committed to the repository."
    )

    @model_validator(mode="after")
    def _uri(self) -> "AudioMetadata":
        if self.uri.startswith("/") or ".." in self.uri.split("/"):
            raise ValueError("audio.uri must be a relative path inside the case directory")
        return self


class LanguageInfo(Contract):
    primary: LanguageTag
    others: list[LanguageTag] = Field(default_factory=list)
    code_mixed: bool = False


class EvidenceAvailability(Contract):
    """What evidence the input actually contains. Checked for consistency with the payload."""

    transcript: bool
    audio: bool
    turn_timestamps: bool
    speaker_labels: bool
    call_start_ts: bool
    call_start_captured: bool
    call_end_captured: bool


class EvaluabilityMetadata(Contract):
    """Upstream-observable quality signals. Not a gold label and not an evaluator judgement."""

    known_issues: list[EvaluabilityIssue] = Field(default_factory=list)
    audio_snr_db: float | None = None
    asr_mean_confidence: Probability | None = None
    notes: str | None = None


class CanonicalInput(Contract):
    schema_version: Literal["canonical_input/1.0.0"] = CANONICAL_INPUT_SCHEMA
    call_id: Id
    input_mode: InputMode
    language: LanguageInfo
    call_start_ts: AwareDatetime | None = None
    transcript: Transcript | None = None
    audio: AudioMetadata | None = None
    evidence_availability: EvidenceAvailability
    evaluability: EvaluabilityMetadata = Field(default_factory=EvaluabilityMetadata)

    @model_validator(mode="after")
    def _invariants(self) -> "CanonicalInput":
        errs: list[str] = []
        t, a, mode = self.transcript, self.audio, self.input_mode
        if mode is InputMode.TRANSCRIPT_ONLY:
            if t is None:
                errs.append("transcript_only input requires a transcript")
            if a is not None:
                errs.append("transcript_only input must not carry audio")
            if t is not None and t.provenance.source is TranscriptSource.PIPELINE_ASR:
                errs.append("transcript_only input cannot carry a pipeline_asr transcript")
        elif mode is InputMode.AUDIO_ONLY:
            if a is None:
                errs.append("audio_only input requires audio")
            if t is not None and t.provenance.source is not TranscriptSource.PIPELINE_ASR:
                errs.append(
                    "audio_only input may only carry a transcript produced by the pipeline's own ASR "
                    "(provenance.source == pipeline_asr)"
                )
        else:
            if t is None or a is None:
                errs.append("audio_transcript input requires both audio and transcript")

        ea = self.evidence_availability
        if ea.transcript != (t is not None):
            errs.append("evidence_availability.transcript disagrees with transcript presence")
        if ea.audio != (a is not None):
            errs.append("evidence_availability.audio disagrees with audio presence")
        if ea.call_start_ts != (self.call_start_ts is not None):
            errs.append("evidence_availability.call_start_ts disagrees with call_start_ts presence")
        if ea.turn_timestamps and (t is None or not t.turns or not all(x.has_timestamps for x in t.turns)):
            errs.append("evidence_availability.turn_timestamps=true but some turns lack start/end")
        if ea.speaker_labels and (t is None or any(x.speaker is Speaker.UNKNOWN for x in t.turns)):
            errs.append("evidence_availability.speaker_labels=true but transcript missing or has unknown speakers")
        if a is not None and t is not None and a.duration_ms is not None:
            ends = [x.end_ms for x in t.turns if x.end_ms is not None]
            if ends and max(ends) > a.duration_ms + 1000:
                errs.append("turn timestamps extend beyond audio duration (+1s tolerance)")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    def turn_map(self) -> dict[str, Turn]:
        return {t.turn_id: t for t in self.transcript.turns} if self.transcript else {}


def derive_evidence_availability(
    *,
    transcript: Transcript | None,
    audio: AudioMetadata | None,
    call_start_ts_present: bool,
    call_start_captured: bool,
    call_end_captured: bool,
) -> EvidenceAvailability:
    """Compute the mechanically derivable availability flags (the two 'captured' flags are judgements)."""
    turns = transcript.turns if transcript else []
    return EvidenceAvailability(
        transcript=transcript is not None,
        audio=audio is not None,
        turn_timestamps=bool(turns) and all(t.has_timestamps for t in turns),
        speaker_labels=transcript is not None and all(t.speaker is not Speaker.UNKNOWN for t in turns),
        call_start_ts=call_start_ts_present,
        call_start_captured=call_start_captured,
        call_end_captured=call_end_captured,
    )
