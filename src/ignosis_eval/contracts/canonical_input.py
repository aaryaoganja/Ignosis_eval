"""NormalizedInput — the output of the shared front end (L0–L3) and the only thing a system sees.

Reconciled with frozen-contract.md §2 (input modes, transcript header, role labels, reliability markers,
evaluation-text rule) and experiment-protocol.md P-2/P-5. Turn numbers are 1-based positions in the
normalized transcript; gold anchors and evidence citations use the same numbering (SD-12).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ignosis_eval.canonical import canonical_json_bytes, sha256_bytes
from ignosis_eval.contracts._base import Contract, NonEmptyStr, Probability, Sha256Hex
from ignosis_eval.contracts.enums import (
    UNIT_MODE_INPUT,
    EvaluabilityStatus,
    InputMode,
    ReasonCode,
    Role,
    TranscriptProvenance,
    UnitMode,
)
from ignosis_eval.contracts.evaluation_record import Finding, GateResult
from ignosis_eval.versions import NORMALIZED_INPUT_SCHEMA


class TranscriptHeader(Contract):
    """§2.3 header. call_start_ts only ever comes from here (R-08); audio metadata is never used."""

    call_start_ts: AwareDatetime | None = None
    transcript_provenance: TranscriptProvenance = TranscriptProvenance.UNKNOWN
    truncated_start: bool = False


class Turn(Contract):
    turn: int = Field(ge=1)
    role: Role
    text: str  # evaluation text (§2.4)
    supplied_text: str | None = None  # HEARD (supplied transcript), when a transcript was supplied
    asr_text: str | None = None  # SAID (our ASR), when audio was transcribed and aligned
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    unreliable: bool = False  # DC-01: reliability marker, UNKNOWN role label, or low turn-level diarization (AJ-05)
    diarization_confidence: Probability | None = None  # turn-level speaker confidence of diarized audio; None otherwise
    language: str | None = None

    @model_validator(mode="after")
    def _times(self) -> "Turn":
        if self.start_s is not None and self.end_s is not None and self.start_s > self.end_s:
            raise ValueError(f"turn {self.turn}: start_s > end_s")
        return self


class AudioRef(Contract):
    """Content-addressed audio reference. The file name never reaches a system: authors may name files after
    items, and a semantic id would leak the expected result. The front end resolves the file privately."""

    sha256: Sha256Hex
    format: Literal["wav", "mp3", "m4a"]


class ASRRef(Contract):
    engine: NonEmptyStr
    model: str | None = None
    version: NonEmptyStr
    params_sha256: Sha256Hex


class FrontendCheck(Contract):
    """Status of one front-end step (DC-xx, pre-check). Pending or unimplemented steps are explicit."""

    check: NonEmptyStr
    status: Literal["done", "pending_signoff", "not_implemented", "not_applicable"]
    blocker: str | None = None
    note: str | None = None


class FrontendResult(Contract):
    evaluability_status: EvaluabilityStatus
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    prechecks: list[GateResult] = Field(default_factory=list)  # G7, G8, G9 (and G1c when computable)
    precheck_findings: list[Finding] = Field(default_factory=list)  # POL-01b string matches
    steps: list[FrontendCheck] = Field(default_factory=list)


INPUT_ALIAS_PATTERN = r"^in-[0-9a-f]{16}$"


def input_alias_for(ni_json: dict) -> str:
    """Opaque alias of an input: a hash of its own content (the alias field excluded). It carries no item id, file
    name, split, pack or other benchmark metadata, and is stable across runs (replay fixtures key on the input)."""
    body = {k: v for k, v in ni_json.items() if k != "input_alias"}
    return "in-" + sha256_bytes(canonical_json_bytes(body))[:16]


class NormalizedInput(Contract):
    """Everything a system (K0 / A / A+ / B) sees. It has no item id, unit id, file name, path, split, pack or
    case-card field; `input_alias` is the only identifier, and it is derived from the content (checked)."""

    schema_version: Literal["normalized_input/2.2.0"] = NORMALIZED_INPUT_SCHEMA
    input_alias: str | None = Field(default=None, pattern=INPUT_ALIAS_PATTERN)  # set on validation
    input_mode: InputMode
    unit_mode: UnitMode
    header: TranscriptHeader = Field(default_factory=TranscriptHeader)
    has_timestamps: bool
    role_mapping_confidence: Probability  # DC-00 call-level speaker/channel -> agent/borrower mapping (AJ-05)
    turns: list[Turn]
    audio: AudioRef | None = None
    asr: ASRRef | None = None
    frontend: FrontendResult

    @model_validator(mode="after")
    def _invariants(self) -> "NormalizedInput":
        errs = []
        if UNIT_MODE_INPUT[self.unit_mode] is not self.input_mode:
            errs.append(f"unit_mode {self.unit_mode} implies input_mode {UNIT_MODE_INPUT[self.unit_mode]}")
        for pos, t in enumerate(self.turns, start=1):
            if t.turn != pos:
                errs.append(f"turn numbers must be 1..n in order (position {pos} has {t.turn})")
                break
        timed = [t.start_s is not None and t.end_s is not None for t in self.turns]
        if self.turns and any(timed) and not all(timed):
            errs.append("timestamps are all-or-none (§2.3)")
        if self.has_timestamps != (bool(self.turns) and all(timed)):
            errs.append("has_timestamps disagrees with the turns")
        if self.input_mode is InputMode.TRANSCRIPT and self.audio is not None:
            errs.append("TRANSCRIPT units carry no audio")
        if self.input_mode in (InputMode.AUDIO, InputMode.AUDIO_TRANSCRIPT) and self.audio is None:
            errs.append(f"{self.input_mode} units require audio")
        if self.input_mode is InputMode.AUDIO and self.header.call_start_ts is not None:
            errs.append("AUDIO units have no transcript header; call_start_ts must be null (R-08)")
        if self.unit_mode is UnitMode.A_T_PLATFORM and \
                self.header.transcript_provenance is not TranscriptProvenance.PLATFORM_LIVE_ASR:
            errs.append("A+T-platform units require transcript_provenance=platform_live_asr")
        alias = input_alias_for(self.model_dump(mode="json"))
        if self.input_alias is None:
            self.input_alias = alias
        elif self.input_alias != alias:
            errs.append("input_alias must be the opaque content alias (never an item id or a file name)")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    def turn_by_number(self, n: int) -> Turn | None:
        return self.turns[n - 1] if 1 <= n <= len(self.turns) else None
