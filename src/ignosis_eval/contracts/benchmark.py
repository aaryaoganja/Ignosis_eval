"""Benchmark item metadata, unit facts and manifests (experiment-protocol P-1, P-5; scoring-spec SD-01).

Layout (see ignosis_eval/benchmark/layout.py):
  dev      bench/dev/items/<item_id>/{item.json, transcript, audio}        (in repository)
  private  $BENCH_PRIVATE_DIR/{holdout,redteam}/items/<item_id>/...      (outside the repository)
Private files are hash-listed in the repository (P-1 rule 5) by a `private` scope BenchManifest.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ignosis_eval.contracts._base import Contract, ItemId, NonEmptyStr, Sha256Hex
from ignosis_eval.contracts.enums import InputMode, Pack, Split, TranscriptProvenance, UnitMode
from ignosis_eval.versions import BENCH_MANIFEST_SCHEMA, GOLD_MANIFEST_SCHEMA, ITEM_META_SCHEMA

AUDIO_UNIT_MODES = frozenset({UnitMode.T_GOLD, UnitMode.T_ASR, UnitMode.A, UnitMode.A_T, UnitMode.A_T_PLATFORM})


class ItemArtifacts(Contract):
    transcript: str | None = None  # .txt or .json (§2.3), relative to the item directory
    audio: str | None = None  # .wav / .mp3 / .m4a
    platform_transcript: str | None = None  # weaker-ASR transcript for A+T-platform (B-07)


class ItemMeta(Contract):
    """One benchmark item. Audio renderings of a textual item (e.g. P-01@audio) are units of the same item
    (T-gold, T-asr, A, A+T[, A+T-platform]) so that they share its content gold and can never cross splits;
    audio-native items (S-01..S-04) have audio units only."""

    schema_version: Literal["item_meta/1.1.0"] = ITEM_META_SCHEMA
    item_id: ItemId
    split: Split
    pack: Pack
    language: Literal["en", "hi", "hi-en", "other"]
    unit_modes: list[UnitMode] = Field(min_length=1)
    artifacts: ItemArtifacts
    synthetic: bool = True
    tuning_only: bool = False  # e.g. G-02-N5: threshold tuning on dev, "never used for scoring claims" (Stage 5 design)

    @model_validator(mode="after")
    def _modes(self) -> "ItemMeta":
        errs = []
        a = self.artifacts
        modes = set(self.unit_modes)
        if len(modes) != len(self.unit_modes):
            errs.append("duplicate unit modes")
        need_transcript = {UnitMode.TRANSCRIPT, UnitMode.T_GOLD, UnitMode.A_T}
        need_audio = AUDIO_UNIT_MODES
        if modes & need_transcript and not a.transcript:
            errs.append("TRANSCRIPT / T-gold / A+T units require a transcript artifact")
        if modes & need_audio and not a.audio:
            errs.append("audio units require an audio artifact")
        if UnitMode.A_T_PLATFORM in modes and not a.platform_transcript:
            errs.append("A+T-platform units require a platform_transcript artifact (B-07)")
        for p in (a.transcript, a.audio, a.platform_transcript):
            if p and (p.startswith("/") or ".." in p.split("/")):
                errs.append(f"artifact path must be relative to the item directory: {p}")
        if self.pack is Pack.REDTEAM and self.split is not Split.REDTEAM:
            errs.append("red-team items belong to the redteam split")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    @property
    def scoring_role(self) -> Literal["scored", "component", "never"]:
        if self.tuning_only:
            return "never"  # tuning copy; its mode gold is derived from actual ASR output (B-06), never scored
        if self.pack is Pack.SNIPPET:
            return "component"  # SD-22 component tests, not an architecture comparison
        if self.pack is Pack.CALIBRATION:
            return "never"  # §12.7 labeler calibration, never scored
        return "scored"


class UnitFacts(Contract):
    """Facts about one unit that the capability table conditions on (derived at manifest build)."""

    item_id: ItemId
    unit_mode: UnitMode
    input_mode: InputMode
    has_call_start_ts: bool
    has_timestamps: bool
    provenance: TranscriptProvenance | None
    truncated_start: bool

    @property
    def unit_id(self) -> str:
        return f"{self.item_id}__{self.unit_mode.value}"


class FileHash(Contract):
    path: NonEmptyStr  # relative to the scope root, POSIX separators
    sha256: Sha256Hex
    kind: Literal["json_canonical", "raw_bytes"]


class ItemEntry(Contract):
    meta: ItemMeta
    files: list[FileHash]
    units: list[UnitFacts]
    content_fingerprint: Sha256Hex


class BenchManifest(Contract):
    schema_version: Literal["bench_manifest/2.1.0"] = BENCH_MANIFEST_SCHEMA
    dataset_name: NonEmptyStr
    dataset_version: NonEmptyStr
    scope: Literal["dev", "private"]
    created_at: AwareDatetime
    created_by: NonEmptyStr
    items: list[ItemEntry]
    dataset_hash: Sha256Hex
    notes: str | None = None

    @model_validator(mode="after")
    def _unique(self) -> "BenchManifest":
        ids = [i.meta.item_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate item ids")
        return self

    def item(self, item_id: str) -> ItemEntry | None:
        return next((i for i in self.items if i.meta.item_id == item_id), None)


class GoldManifestEntry(Contract):
    item_id: ItemId
    split: Split
    path: NonEmptyStr  # relative to the gold directory of the scope
    sha256: Sha256Hex


class GoldManifest(Contract):
    schema_version: Literal["gold_manifest/2.0.0"] = GOLD_MANIFEST_SCHEMA
    gold_version: NonEmptyStr
    scope: Literal["dev", "private"]
    dataset_name: NonEmptyStr
    dataset_version: NonEmptyStr
    bench_manifest_sha256: Sha256Hex
    labeling_protocol_version: NonEmptyStr
    frozen_at: AwareDatetime
    frozen_by: NonEmptyStr
    entries: list[GoldManifestEntry]
    gold_hash: Sha256Hex

    def entry(self, item_id: str) -> GoldManifestEntry | None:
        return next((e for e in self.entries if e.item_id == item_id), None)
