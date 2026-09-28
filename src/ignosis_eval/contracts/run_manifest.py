"""Run manifest, run completion record and the on-disk run layout (shared by runner and scorer).

    runs/<run_id>/manifest.json                       RunManifest (written once, before any evaluation)
    runs/<run_id>/<item_id>/rep_<k>/normalized_input.json
    runs/<run_id>/<item_id>/rep_<k>/llm_requests.jsonl
    runs/<run_id>/<item_id>/rep_<k>/llm_responses.jsonl
    runs/<run_id>/<item_id>/rep_<k>/evaluation_record.json   (absent if the evaluator failed)
    runs/<run_id>/<item_id>/rep_<k>/timing.json
    runs/<run_id>/<item_id>/rep_<k>/usage.json
    runs/<run_id>/<item_id>/rep_<k>/errors.json              (always written; [] when no error)
    runs/<run_id>/completion.json                     RunCompletion (written once, at the end)
    scoring/<run_id>/item_scores.csv | metrics.json | discordance_tables.csv | scoring_manifest.json

Repetitions are 1-based (rep_1 .. rep_R). Every file is written exactly once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from ignosis_eval.contracts._base import GIT_SHA_PATTERN, Contract, NonEmptyStr, Sha256Hex
from ignosis_eval.contracts.enums import EvaluatorArchitecture, Split
from ignosis_eval.versions import RUN_MANIFEST_SCHEMA

MANIFEST_FILE = "manifest.json"
COMPLETION_FILE = "completion.json"
NORMALIZED_INPUT_FILE = "normalized_input.json"
LLM_REQUESTS_FILE = "llm_requests.jsonl"
LLM_RESPONSES_FILE = "llm_responses.jsonl"
RECORD_FILE = "evaluation_record.json"
TIMING_FILE = "timing.json"
USAGE_FILE = "usage.json"
ERRORS_FILE = "errors.json"
REP_FILES = (NORMALIZED_INPUT_FILE, LLM_REQUESTS_FILE, LLM_RESPONSES_FILE, RECORD_FILE, TIMING_FILE, USAGE_FILE,
             ERRORS_FILE)
SCORING_FILES = ("item_scores.csv", "metrics.json", "discordance_tables.csv", "scoring_manifest.json")


def rep_dir(run_dir: Path, item_id: str, rep: int) -> Path:
    if rep < 1:
        raise ValueError("repetitions are 1-based")
    return Path(run_dir) / item_id / f"rep_{rep}"


class RetryPolicy(Contract):
    max_attempts: int = Field(ge=1)
    backoff_initial_s: float = Field(ge=0)
    backoff_multiplier: float = Field(ge=1)
    retry_on: list[str] = Field(default_factory=list)


class EvaluatorRunConfig(Contract):
    name: NonEmptyStr
    version: NonEmptyStr
    architecture: EvaluatorArchitecture
    llm_backend: Literal["none", "mock", "anthropic"]
    model_id: str | None
    temperature: float | None
    top_p: float | None = None
    max_output_tokens: int | None = None
    prompt_hashes: dict[str, Sha256Hex] = Field(default_factory=dict)
    retry_policy: RetryPolicy
    options: dict[str, Any] = Field(default_factory=dict)
    config_hash: Sha256Hex

    @model_validator(mode="after")
    def _llm_fields(self) -> "EvaluatorRunConfig":
        if self.llm_backend != "none" and (not self.model_id or self.temperature is None):
            raise ValueError("LLM-backed evaluators must record model_id and temperature")
        return self


class DatasetRef(Contract):
    name: NonEmptyStr
    version: NonEmptyStr
    split: Split
    benchmark_manifest_sha256: Sha256Hex
    dataset_hash: Sha256Hex
    split_hash: Sha256Hex
    item_ids: list[NonEmptyStr] = Field(min_length=1)

    @property
    def n_items(self) -> int:
        return len(self.item_ids)


class GoldRef(Contract):
    gold_version: NonEmptyStr
    gold_manifest_sha256: Sha256Hex
    gold_hash: Sha256Hex
    split_hash: Sha256Hex


class ProfileRef(Contract):
    profile_id: NonEmptyStr
    profile_version: NonEmptyStr
    rubric_version: NonEmptyStr
    sha256: Sha256Hex
    path: NonEmptyStr
    status: Literal["placeholder", "draft", "frozen"]


class GitInfo(Contract):
    commit: str = Field(pattern=GIT_SHA_PATTERN)
    branch: str | None = None
    dirty: bool


class ASRInfo(Contract):
    engine: NonEmptyStr
    model: str | None = None
    version: NonEmptyStr
    config: dict[str, Any] = Field(default_factory=dict)


class AudioRenderingSummary(Contract):
    n_items_with_audio: int = Field(ge=0)
    renderings: list[dict[str, Any]] = Field(default_factory=list)  # distinct rendering configs in the split


class Randomization(Contract):
    seed: int
    item_order: Literal["manifest", "shuffled"]
    rep_seed_derivation: NonEmptyStr


class RunManifest(Contract):
    schema_version: Literal["run_manifest/1.0.0"] = RUN_MANIFEST_SCHEMA
    run_id: NonEmptyStr
    created_at: AwareDatetime
    purpose: str | None = None
    official: bool
    dataset: DatasetRef
    gold: GoldRef | None
    rubric_version: NonEmptyStr
    profile: ProfileRef
    evaluator: EvaluatorRunConfig
    component_versions: dict[str, str]
    git: GitInfo
    asr: ASRInfo | None
    audio_rendering: AudioRenderingSummary
    repetitions: int = Field(ge=1)
    randomization: Randomization
    environment: dict[str, str]
    invocation: list[str]

    @model_validator(mode="after")
    def _consistency(self) -> "RunManifest":
        if self.rubric_version != self.profile.rubric_version:
            raise ValueError("rubric_version must equal profile.rubric_version")
        if len(set(self.dataset.item_ids)) != len(self.dataset.item_ids):
            raise ValueError("duplicate item ids in run manifest")
        if self.official and (self.gold is None or self.git.dirty):
            raise ValueError("official runs require frozen gold and a clean git tree")
        return self


class RunCompletion(Contract):
    run_id: NonEmptyStr
    status: Literal["completed", "failed"]
    finished_at: AwareDatetime
    n_item_reps_expected: int = Field(ge=0)
    n_records_written: int = Field(ge=0)
    n_item_reps_with_errors: int = Field(ge=0)
    benchmark_verified_after_run: bool
    gold_verified_after_run: bool | None
    failure_reason: str | None = None
