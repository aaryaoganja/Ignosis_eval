"""Shared front end (L0–L3): intake -> ASR (cached) -> normalized turns -> role gate -> evaluability/pre-checks.

One implementation shared by every system (experiment-protocol P-2). Unit modes (P-5):
  TRANSCRIPT / T-gold   supplied transcript as TRANSCRIPT
  T-asr                 our cached ASR output supplied as TRANSCRIPT, provenance offline_asr
  A                     audio only: ASR text, diarized roles; no header (call_start_ts never from audio, R-08)
  A+T                   audio + supplied transcript (evaluation text per §2.4; divergence check not built yet)
  A+T-platform          audio + weaker-ASR transcript, provenance platform_live_asr
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from ignosis_eval.canonical import sha256_bytes
from ignosis_eval.contracts.benchmark import ItemMeta, UnitFacts
from ignosis_eval.contracts.canonical_input import ASRRef, AudioRef, NormalizedInput, TranscriptHeader, Turn
from ignosis_eval.contracts.enums import UNIT_MODE_INPUT, Role, TranscriptProvenance, UnitMode
from ignosis_eval.contracts.unit_alias import new_unit_alias
from ignosis_eval.pipeline.asr import ASRAdapter, ASRUnavailableError
from ignosis_eval.pipeline.intake import ParsedTranscript, parse_transcript
from ignosis_eval.pipeline.prechecks import run_frontend
from ignosis_eval.spec.loader import Spec


class NormalizationError(RuntimeError):
    pass


def _transcript_path(meta: ItemMeta, item_dir: Path, mode: UnitMode) -> Path | None:
    rel = meta.artifacts.platform_transcript if mode is UnitMode.A_T_PLATFORM else meta.artifacts.transcript
    return (item_dir / rel) if rel else None


def unit_facts(meta: ItemMeta, item_dir: Path) -> list[UnitFacts]:
    """Capability-relevant facts per unit, computed from the artifacts (used by manifests and gold derivation)."""
    out: list[UnitFacts] = []
    for mode in meta.unit_modes:
        tp = _transcript_path(meta, item_dir, mode)
        parsed: ParsedTranscript | None = parse_transcript(tp) if tp else None
        header = parsed.header if parsed else TranscriptHeader()
        if mode is UnitMode.A:
            out.append(UnitFacts(item_id=meta.item_id, unit_mode=mode, input_mode=UNIT_MODE_INPUT[mode],
                                 has_call_start_ts=False, has_timestamps=True, provenance=None,
                                 truncated_start=header.truncated_start))
            continue
        provenance = TranscriptProvenance.OFFLINE_ASR if mode is UnitMode.T_ASR else header.transcript_provenance
        has_ts = True if mode is UnitMode.T_ASR else bool(parsed and parsed.has_timestamps)
        out.append(UnitFacts(item_id=meta.item_id, unit_mode=mode, input_mode=UNIT_MODE_INPUT[mode],
                             has_call_start_ts=header.call_start_ts is not None, has_timestamps=has_ts,
                             provenance=provenance, truncated_start=header.truncated_start))
    return out


def _audio_ref(meta: ItemMeta, item_dir: Path) -> tuple[AudioRef, Path]:
    assert meta.artifacts.audio
    p = item_dir / meta.artifacts.audio
    if not p.exists():
        raise NormalizationError(f"audio file missing: {meta.artifacts.audio}")
    fmt = p.suffix.lower().lstrip(".")
    if fmt not in ("wav", "mp3", "m4a"):
        raise NormalizationError(f"unsupported audio format {fmt!r} (wav/mp3/m4a)")
    return AudioRef(sha256=sha256_bytes(p.read_bytes()), format=fmt), p  # type: ignore[arg-type]


_PAIR_ID = r"(?:MP|CP|AP)-\d{2}"


def assert_no_identifiers(meta: ItemMeta, item_dir: Path, turns: list[Turn]) -> None:
    """Fail closed if the text a system will see names the item: its id, its directory, an artifact file stem that
    is not a plain word, or a pair id. Systems receive turn text only, so this is the last place an identifier could
    leak into a prompt (bench check B017 reports the wider set: every design id and every rubric check id)."""
    stems = {Path(a).stem for a in (meta.artifacts.transcript, meta.artifacts.audio, meta.artifacts.platform_transcript)
             if a}
    tokens = {meta.item_id, item_dir.name} | {s for s in stems if re.search(r"[\d-]", s)}
    alts = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
    pattern = re.compile(rf"(?<![A-Za-z0-9-])(?:{alts}|{_PAIR_ID})(?![A-Za-z0-9-])", re.IGNORECASE)
    for t in turns:
        m = pattern.search(t.text)
        if m:
            raise NormalizationError(f"turn {t.turn} contains the identifier {m.group(0)!r}; systems must never see "
                                     "item ids, file names or pair ids (fix the transcript)")


def _transcribe_as_alias(asr: ASRAdapter, audio_path: Path, sha: str, alias: str):
    """P-17 rule 3: the rendered audio is renamed to its alias before any ASR step, so no item id or source file
    name reaches the ASR adapter."""
    with tempfile.TemporaryDirectory(prefix="p17-") as tmp:
        aliased = Path(tmp) / f"{alias}{audio_path.suffix.lower()}"
        shutil.copyfile(audio_path, aliased)
        return asr.transcribe(aliased, sha)


def supplied_turns(parsed: ParsedTranscript) -> list[Turn]:
    """Turns of a supplied transcript (TRANSCRIPT, T-gold, A+T, A+T-platform units)."""
    # DC-01 (AJ-05): an UNKNOWN-labeled turn is span-unreliable; it does not lower call-level role confidence
    return [Turn(turn=i, role=pt.role, text=pt.text, supplied_text=pt.text, start_s=pt.start_s, end_s=pt.end_s,
                 unreliable=pt.unreliable or pt.role is Role.UNKNOWN) for i, pt in enumerate(parsed.turns, start=1)]


def audio_ref_from_bytes(data: bytes, fmt: str) -> AudioRef:
    fmt = fmt.lower().lstrip(".")
    if fmt not in ("wav", "mp3", "m4a"):
        raise NormalizationError(f"unsupported audio format {fmt!r} (wav/mp3/m4a)")
    return AudioRef(sha256=sha256_bytes(data), format=fmt)  # type: ignore[arg-type]


def normalize_supplied(parsed: ParsedTranscript, mode: UnitMode, spec: Spec, *, audio: AudioRef | None = None,
                       unit_alias: str | None = None) -> NormalizedInput:
    """A supplied transcript that is not a benchmark item (the review app): TRANSCRIPT, or A+T with the audio's
    fingerprint. Same turns and the same front end as `build_normalized_input`; there is no item id to screen."""
    if mode not in (UnitMode.TRANSCRIPT, UnitMode.A_T):
        raise NormalizationError(f"{mode.value} needs ASR (B-06): only TRANSCRIPT and A+T take a supplied transcript")
    if (mode is UnitMode.A_T) != (audio is not None):
        raise NormalizationError("A+T needs the audio file; TRANSCRIPT takes none")
    turns = supplied_turns(parsed)
    input_mode = UNIT_MODE_INPUT[mode]
    frontend = run_frontend(turns, parsed.header, input_mode, spec, role_mapping_confidence=1.0, diarized=False)
    return NormalizedInput(unit_alias=unit_alias or new_unit_alias(), input_mode=input_mode, unit_mode=mode,
                           header=parsed.header, has_timestamps=parsed.has_timestamps, role_mapping_confidence=1.0,
                           turns=turns, audio=audio, frontend=frontend)


def build_normalized_input(meta: ItemMeta, item_dir: Path, mode: UnitMode, spec: Spec,
                           asr: ASRAdapter | None = None, *, unit_alias: str | None = None) -> NormalizedInput:
    """`unit_alias` is the run's opaque alias for this unit (P-17); a fresh random one when omitted."""
    if mode not in meta.unit_modes:
        raise NormalizationError(f"{meta.item_id} has no {mode} unit")
    alias = unit_alias or new_unit_alias()
    input_mode = UNIT_MODE_INPUT[mode]
    tp = _transcript_path(meta, item_dir, mode)
    parsed = parse_transcript(tp) if tp else None
    header = parsed.header if parsed else TranscriptHeader()
    turns: list[Turn] = []
    audio_ref = asr_ref = None
    mapping_confidence = 1.0  # DC-00: supplied transcript labels map roles for the whole call (AJ-05)

    if mode in (UnitMode.TRANSCRIPT, UnitMode.T_GOLD, UnitMode.A_T, UnitMode.A_T_PLATFORM):
        assert parsed is not None
        turns = supplied_turns(parsed)
        has_ts = parsed.has_timestamps
    else:  # T-asr, A: evaluation text is our ASR
        if asr is None:
            raise ASRUnavailableError("audio unit but no ASR adapter configured (B-06 pending)")
        audio_ref, audio_path = _audio_ref(meta, item_dir)
        res = _transcribe_as_alias(asr, audio_path, audio_ref.sha256, alias)
        asr_ref = ASRRef(engine=res.engine, model=res.model, version=res.version, params_sha256=res.params_sha256)
        mapping_confidence = res.mapping_confidence
        for i, at in enumerate(res.turns, start=1):
            # turn-level diarization threshold is PENDING (B-11): only UNKNOWN turns are marked unreliable here
            turns.append(Turn(turn=i, role=at.role, text=at.text, asr_text=at.text, start_s=at.start_s,
                              end_s=at.end_s, diarization_confidence=at.diarization_confidence,
                              unreliable=at.role is Role.UNKNOWN))
        has_ts = bool(turns)
        if mode is UnitMode.T_ASR:
            # P-5: our ASR supplied *as a transcript* with provenance offline_asr; header facts from the item.
            header = TranscriptHeader(call_start_ts=header.call_start_ts, truncated_start=header.truncated_start,
                                      transcript_provenance=TranscriptProvenance.OFFLINE_ASR)
            for turn in turns:
                turn.supplied_text = turn.text
            audio_ref = None
        else:
            header = TranscriptHeader()  # AUDIO mode carries no transcript header
    if mode in (UnitMode.A_T, UnitMode.A_T_PLATFORM):
        audio_ref, _ = _audio_ref(meta, item_dir)
    if mode is UnitMode.A_T_PLATFORM and header.transcript_provenance is not TranscriptProvenance.PLATFORM_LIVE_ASR:
        raise NormalizationError("A+T-platform transcripts must declare transcript_provenance: platform_live_asr")
    assert_no_identifiers(meta, item_dir, turns)
    frontend = run_frontend(turns, header, input_mode, spec, role_mapping_confidence=mapping_confidence,
                            diarized=mode in (UnitMode.T_ASR, UnitMode.A))
    return NormalizedInput(unit_alias=alias, input_mode=input_mode, unit_mode=mode, header=header,
                           has_timestamps=has_ts, role_mapping_confidence=mapping_confidence, turns=turns,
                           audio=audio_ref, asr=asr_ref, frontend=frontend)
