"""Benchmark metadata and manifest contracts.

Case metadata is hidden from evaluators (it lives in case cards and manifests, which are protected
paths). Evaluators only ever receive the Canonical Input.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ignosis_eval.contracts._base import CaseId, Contract, Id, LanguageTag, NonEmptyStr, Sha256Hex
from ignosis_eval.contracts.enums import Capability, InputMode, SourceKind, Split
from ignosis_eval.versions import BENCHMARK_MANIFEST_SCHEMA, GOLD_MANIFEST_SCHEMA


class CapabilityBoundaries(Contract):
    """What the case needs to be fully evaluable, and which single modality would suffice."""

    required_capabilities: list[Capability] = Field(default_factory=list)
    transcript_sufficient: bool
    audio_sufficient: bool
    notes: str | None = None


class CaseMetadata(Contract):
    case_id: CaseId
    split: Split
    scenario: NonEmptyStr
    category: NonEmptyStr
    intended_modality: InputMode
    language: LanguageTag
    synthetic: bool
    source_kind: SourceKind
    pair_id: Id | None = None
    pair_role: str | None = None
    attribution_pair_id: Id | None = None
    judge_bait: bool = False
    judge_bait_kind: str | None = None
    capability_boundaries: CapabilityBoundaries
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistency(self) -> "CaseMetadata":
        if self.synthetic != self.source_kind.is_synthetic:
            raise ValueError("synthetic flag disagrees with source_kind")
        if (self.pair_id is None) != (self.pair_role is None):
            raise ValueError("pair_id and pair_role must be set together")
        if self.judge_bait != (self.judge_bait_kind is not None):
            raise ValueError("judge_bait and judge_bait_kind must be set together")
        return self


class FileHash(Contract):
    path: NonEmptyStr  # relative to the benchmark root, POSIX separators
    sha256: Sha256Hex
    kind: Literal["json_canonical", "raw_bytes"]


class CaseEntry(Contract):
    metadata: CaseMetadata
    input_path: NonEmptyStr
    case_card_path: NonEmptyStr
    gold_path: str | None = None  # relative path if a gold file exists; gold hashes live in the gold manifest
    files: list[FileHash]  # every benchmark file of the case except gold (input, audio, sidecars, card)
    content_fingerprint: Sha256Hex  # modality content only; used to detect cross-split leakage


class BenchmarkManifest(Contract):
    schema_version: Literal["benchmark_manifest/1.0.0"] = BENCHMARK_MANIFEST_SCHEMA
    dataset_name: NonEmptyStr
    dataset_version: NonEmptyStr
    created_at: AwareDatetime
    created_by: NonEmptyStr
    cases: list[CaseEntry]
    split_hashes: dict[Split, Sha256Hex]
    dataset_hash: Sha256Hex
    notes: str | None = None

    @model_validator(mode="after")
    def _unique(self) -> "BenchmarkManifest":
        ids = [c.metadata.case_id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate case ids in benchmark manifest")
        return self

    def case(self, case_id: str) -> CaseEntry | None:
        return next((c for c in self.cases if c.metadata.case_id == case_id), None)

    def split_cases(self, split: Split) -> list[CaseEntry]:
        return [c for c in self.cases if c.metadata.split is split]


class GoldManifestEntry(Contract):
    item_id: CaseId
    split: Split
    path: NonEmptyStr
    sha256: Sha256Hex


class GoldManifest(Contract):
    schema_version: Literal["gold_manifest/1.0.0"] = GOLD_MANIFEST_SCHEMA
    gold_version: NonEmptyStr
    dataset_name: NonEmptyStr
    dataset_version: NonEmptyStr
    benchmark_manifest_sha256: Sha256Hex
    labeling_protocol_version: NonEmptyStr
    frozen_at: AwareDatetime
    frozen_by: NonEmptyStr
    entries: list[GoldManifestEntry]
    split_hashes: dict[Split, Sha256Hex]
    gold_hash: Sha256Hex

    def entry(self, item_id: str) -> GoldManifestEntry | None:
        return next((e for e in self.entries if e.item_id == item_id), None)


class HoldoutRegistryEntry(Contract):
    case_id: CaseId
    content_fingerprint: Sha256Hex
    registered_at: AwareDatetime


class HoldoutRegistry(Contract):
    """Append-only record of every case ever placed in the holdout split.

    Once registered, a case id (or its content) may never appear in another split, and a registered
    case may never disappear. Guards against holdout cases leaking into dev/tuning.
    """

    schema_version: Literal["holdout_registry/1.0.0"] = "holdout_registry/1.0.0"
    entries: list[HoldoutRegistryEntry] = Field(default_factory=list)

    def ids(self) -> set[str]:
        return {e.case_id for e in self.entries}
