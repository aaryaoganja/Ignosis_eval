"""Normalization: benchmark Canonical Input -> the normalized input handed to the evaluator.

* transcript_only / audio_transcript: identity (the provided transcript is used as-is).
* audio_only: the pipeline ASR produces a transcript (provenance.source = pipeline_asr); if ASR yields
  nothing, the input stays transcript-less and `asr_failed` is added to the evaluability signals.
The audio file hash is verified before ASR. The normalized input is persisted per repetition.
"""

from __future__ import annotations

from pathlib import Path

from ignosis_eval.canonical import sha256_bytes
from ignosis_eval.contracts.canonical_input import (
    CanonicalInput,
    Transcript,
    TranscriptProvenance,
    derive_evidence_availability,
)
from ignosis_eval.contracts.enums import EvaluabilityIssue, InputMode, TimestampSource, TranscriptSource
from ignosis_eval.pipeline.asr import ASRClient


class NormalizationError(RuntimeError):
    pass


def normalize_input(inp: CanonicalInput, case_dir: Path, asr: ASRClient | None) -> CanonicalInput:
    if inp.input_mode is not InputMode.AUDIO_ONLY or inp.transcript is not None:
        return inp
    assert inp.audio is not None
    audio_path = Path(case_dir) / inp.audio.uri
    if not audio_path.exists():
        raise NormalizationError(f"audio file missing: {inp.audio.uri}")
    if sha256_bytes(audio_path.read_bytes()) != inp.audio.sha256:
        raise NormalizationError(f"audio sha256 mismatch for {inp.audio.uri}")
    if asr is None:
        raise NormalizationError("audio_only input but no ASR client configured")
    turns = asr.transcribe(inp.audio, audio_path)
    ev = inp.evaluability.model_copy(deep=True)
    if not turns:
        if EvaluabilityIssue.ASR_FAILED not in ev.known_issues:
            ev.known_issues.append(EvaluabilityIssue.ASR_FAILED)
        return inp.model_copy(update={"evaluability": ev})
    transcript = Transcript(
        provenance=TranscriptProvenance(
            source=TranscriptSource.PIPELINE_ASR, asr_engine=asr.engine, asr_model=asr.model,
            asr_version=asr.version, diarization="model_diarized", timestamp_source=TimestampSource.ASR_ALIGNED,
            produced_at=None,  # left null on purpose: normalized inputs must be byte-reproducible
            derived_from_audio_sha256=inp.audio.sha256),
        turns=turns)
    availability = derive_evidence_availability(
        transcript=transcript, audio=inp.audio, call_start_ts_present=inp.call_start_ts is not None,
        call_start_captured=inp.evidence_availability.call_start_captured,
        call_end_captured=inp.evidence_availability.call_end_captured)
    data = inp.model_dump()
    data.update(transcript=transcript.model_dump(), evidence_availability=availability.model_dump(),
                evaluability=ev.model_dump())
    return CanonicalInput.model_validate(data)
