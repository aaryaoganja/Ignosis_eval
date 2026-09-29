"""Run manifest and completion record (frozen-contract §17, experiment-protocol P-3/P-6/P-8/P-10/P-11).

Storage layout (P-11), all files write-once:
    runs/<run_id>/manifest.json
    blinding/<run_id>/alias_mapping.json                   (P-10; hash in the run manifest; outside the scorer's
                                                            input path; revealed only after scoring)
    blinded/<run_id>/SYS-<n>/<item_id>__<mode>/rep_<k>/... (P-10 aliased scoring view; see BlindViewManifest)
    runs/<run_id>/<system>/<item_id>__<mode>/rep_<k>/{normalized_input.json, llm_requests.jsonl,
        llm_responses.jsonl, evaluation_record.json, timing.json, usage.json, errors.json}
    runs/<run_id>/A+/<item_id>__<mode>/rep_<k>/derivation_log.json
    runs/<run_id>/completion.json
    scoring/<run_id>/{item_scores.csv, metrics.json, discordance_tables.csv, human_checks.csv}
Tuning runs use run ids `dev-<round>-<timestamp>`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from ignosis_eval.contracts._base import GIT_SHA_PATTERN, Contract, NonEmptyStr, Sha256Hex
from ignosis_eval.contracts.benchmark import FileHash
from ignosis_eval.contracts.enums import Split, System
from ignosis_eval.versions import BLIND_VIEW_SCHEMA, RUN_MANIFEST_SCHEMA, SCORING_MANIFEST_SCHEMA

MANIFEST_FILE = "manifest.json"
COMPLETION_FILE = "completion.json"
ALIAS_MAPPING_FILE = "alias_mapping.json"
NORMALIZED_INPUT_FILE = "normalized_input.json"
LLM_REQUESTS_FILE = "llm_requests.jsonl"
LLM_RESPONSES_FILE = "llm_responses.jsonl"
RECORD_FILE = "evaluation_record.json"
TIMING_FILE = "timing.json"
USAGE_FILE = "usage.json"
ERRORS_FILE = "errors.json"
DERIVATION_LOG_FILE = "derivation_log.json"
REP_FILES = (NORMALIZED_INPUT_FILE, LLM_REQUESTS_FILE, LLM_RESPONSES_FILE, RECORD_FILE, TIMING_FILE, USAGE_FILE,
             ERRORS_FILE)
VIEW_MANIFEST_FILE = "view_manifest.json"
VIEW_REP_FILES = (NORMALIZED_INPUT_FILE, RECORD_FILE, TIMING_FILE, USAGE_FILE)
SCORING_FILES = ("item_scores.csv", "metrics.json", "discordance_tables.csv", "human_checks.csv",
                 "scoring_manifest.json")
REVEAL_FILE = "reveal.json"
DEFAULT_REPETITIONS = 5  # P-5: k = 5 for A and B; A+ inherits A's reps; K0 runs once (replicated)


def unit_dir_name(item_id: str, unit_mode: str) -> str:
    return f"{item_id}__{unit_mode}"


def rep_dir(run_dir: Path, system: str, item_id: str, unit_mode: str, rep: int) -> Path:
    if rep < 1:
        raise ValueError("repetitions are 1-based")
    return Path(run_dir) / system / unit_dir_name(item_id, unit_mode) / f"rep_{rep}"


class TransportRetryPolicy(Contract):
    """P-3: transport errors get up to 3 retries with backoff; logged; not counted as retries."""

    max_retries: int = Field(default=3, ge=0, le=3)
    backoff_initial_s: float = Field(default=1.0, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1)


class SystemConfig(Contract):
    system: System
    version: NonEmptyStr
    llm_backend: Literal["none", "mock_replay", "anthropic"]
    model_snapshot_id: str | None = None
    temperature: float | None = None
    seed: int | None = None
    seed_supported: bool | None = None
    max_tokens: int | None = None
    structured_output: bool = False
    prompt_hashes: dict[str, Sha256Hex] = Field(default_factory=dict)
    rubric_prompt_template_version: str | None = None
    transport_retry: TransportRetryPolicy | None = None
    schema_retries: int = 0
    consistency_rerun: Literal[False] = False  # R-05: disabled for all architectures during benchmark runs
    derived_from: Literal["A"] | None = None  # A+ is derived from A's stored raw output (§11, P-4)

    @model_validator(mode="after")
    def _rules(self) -> "SystemConfig":
        errs = []
        llm = self.system in (System.A, System.B)
        if llm:
            if self.llm_backend == "none":
                errs.append(f"{self.system} requires an LLM backend")
            if not self.model_snapshot_id:
                errs.append(f"{self.system} requires a pinned model_snapshot_id (P-3)")
            if self.model_snapshot_id and "latest" in self.model_snapshot_id.lower():
                errs.append("model aliases like 'latest' are forbidden (P-3)")
            if self.temperature != 0:
                errs.append("temperature must be 0 (P-3)")
            if self.schema_retries != 1:
                errs.append("schema-invalid output gets exactly 1 retry (P-3)")
            if self.transport_retry is None:
                errs.append("transport retry policy must be recorded (P-3)")
        else:
            if self.llm_backend != "none" or self.prompt_hashes:
                errs.append(f"{self.system} makes no LLM call and has no prompt of its own")
        if self.system is System.A_PLUS and self.derived_from != "A":
            errs.append("A+ must be derived from A (P-4)")
        if self.system is not System.A_PLUS and self.derived_from is not None:
            errs.append("only A+ is derived")
        if errs:
            raise ValueError("; ".join(errs))
        return self


class DatasetRef(Contract):
    name: NonEmptyStr
    version: NonEmptyStr
    split: Split
    scope: Literal["dev", "private"]
    bench_manifest_sha256: Sha256Hex
    dataset_hash: Sha256Hex
    registries_sha256: Sha256Hex
    units: list[NonEmptyStr] = Field(min_length=1)  # "<item_id>__<mode>"


class GoldRef(Contract):
    gold_version: NonEmptyStr
    gold_manifest_sha256: Sha256Hex
    gold_hash: Sha256Hex


class SpecRef(Contract):
    contract_version: NonEmptyStr
    rubric_version: NonEmptyStr
    rubric_sha256: Sha256Hex
    profile_id: NonEmptyStr
    profile_version: NonEmptyStr
    profile_sha256: Sha256Hex
    profile_path: NonEmptyStr
    profile_is_canonical: bool
    lexicons_sha256: Sha256Hex
    spec_file_sha256: dict[str, Sha256Hex]


class ComponentRef(Contract):
    """An external component whose identity may still be pending sign-off (B-05..B-09)."""

    status: Literal["configured", "mock", "pending_signoff", "not_used"]
    engine: str | None = None
    version: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    blocker: str | None = None


class GitInfo(Contract):
    commit: str = Field(pattern=GIT_SHA_PATTERN)
    branch: str | None = None
    dirty: bool
    tag: str | None = None  # a tag pointing at the evaluator commit (P-8 step 1)


class HashVerification(Contract):
    """P-8 step 3: hashes verified before the run (recorded; H7)."""

    verified_at: AwareDatetime
    bench_manifest: bool
    item_files: bool
    private_hash_list: bool | None  # None for dev runs
    gold: bool
    rubric: bool
    profile: bool
    lexicons: bool


class PendingRef(Contract):
    path: NonEmptyStr
    blocker: str | None = None
    blocks_locked_run: bool


class RunManifest(Contract):
    schema_version: Literal["run_manifest/3.0.0"] = RUN_MANIFEST_SCHEMA
    run_id: NonEmptyStr
    kind: Literal["dev", "dev_tuning", "locked_holdout", "locked_redteam"]
    locked: bool
    tuning_round: int | None = None
    created_at: AwareDatetime
    purpose: str | None = None
    dataset: DatasetRef
    gold: GoldRef
    spec: SpecRef
    systems: list[SystemConfig] = Field(min_length=1)
    asr: ComponentRef
    diarization: ComponentRef
    telephony_simulation: ComponentRef
    price_snapshot: ComponentRef
    base_seed: int
    repetitions: int = Field(ge=1)
    ordering: NonEmptyStr  # P-6 algorithm identifier
    alias_mapping_sha256: Sha256Hex  # P-10 system aliases
    unit_alias_mapping_sha256: Sha256Hex  # P-17 opaque unit aliases (frozen-contract §17 "alias mapping hash")
    p17_payload_strings_checked: int = Field(ge=1)  # P-17 rule 4 pre-run payload test (a failure aborts the run)
    git: GitInfo
    hash_verification: HashVerification
    pending_signoff: list[PendingRef] = Field(default_factory=list)
    frontend_steps_not_implemented: list[str] = Field(default_factory=list)
    component_versions: dict[str, str] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    invocation: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules(self) -> "RunManifest":
        errs = []
        names = [s.system for s in self.systems]
        if len(names) != len(set(names)):
            errs.append("duplicate systems")
        if System.A_PLUS in names and System.A not in names:
            errs.append("A+ requires A in the same run (derived per rep, P-6)")
        private = self.dataset.split in (Split.HOLDOUT, Split.REDTEAM)
        if private != (self.dataset.scope == "private"):
            errs.append("holdout/red-team data is private scope; dev is in-repo scope (P-1)")
        if private and not self.locked:
            errs.append("holdout and red-team runs must be locked (P-1, P-8, H7)")
        if self.locked:
            if self.kind not in ("locked_holdout", "locked_redteam"):
                errs.append("locked runs are holdout or red-team runs")
            if not self.git.tag or self.git.dirty:
                errs.append("locked runs require a tagged, clean evaluator commit (P-8 step 1)")
            hv = self.hash_verification
            if not all([hv.bench_manifest, hv.item_files, hv.private_hash_list is True, hv.gold, hv.rubric,
                        hv.profile, hv.lexicons]):
                errs.append("locked runs require every hash verified before the run (P-8 step 3)")
            if not self.spec.profile_is_canonical:
                errs.append("locked runs must use the spec-pack profile")
            if any(p.blocks_locked_run for p in self.pending_signoff):
                errs.append("locked runs require all blocking PENDING_HUMAN_SIGNOFF items resolved")
            if any(s.llm_backend == "mock_replay" for s in self.systems):
                errs.append("locked runs cannot use the mock replay backend")
        elif self.kind in ("locked_holdout", "locked_redteam"):
            errs.append("locked kinds require locked=true")
        if self.kind == "dev_tuning" and self.tuning_round is None:
            errs.append("tuning runs record their round (P-7)")
        if errs:
            raise ValueError("; ".join(errs))
        return self


class H7Audit(Contract):
    """What the scorer needs to audit H7 without seeing system identities."""

    kind: Literal["dev", "dev_tuning", "locked_holdout", "locked_redteam"]
    locked: bool
    git_tag: str | None
    git_dirty: bool
    hash_verification: HashVerification
    run_created_at: AwareDatetime


class BlindViewManifest(Contract):
    """P-10 aliased scoring view. It never names a system: aliases SYS-1..n only. K0's single run is
    replicated as reps 1..k; A+ timing is A's latency plus the derivation time and A+ usage is A's (SD-24/25)."""

    schema_version: Literal["blind_view/1.0.0"] = BLIND_VIEW_SCHEMA
    run_id: NonEmptyStr
    run_manifest_sha256: Sha256Hex
    alias_mapping_sha256: Sha256Hex
    aliases: list[NonEmptyStr] = Field(min_length=1)
    repetitions: int = Field(ge=1)
    base_seed: int
    dataset: DatasetRef
    gold: GoldRef
    spec: SpecRef
    model_snapshot_ids: list[str] = Field(default_factory=list)
    mock_backend: bool
    pending_signoff: list[PendingRef] = Field(default_factory=list)
    h7: H7Audit
    created_at: AwareDatetime
    files: list[FileHash]
    view_hash: Sha256Hex


class ScoringManifest(Contract):
    schema_version: Literal["scoring_manifest/2.0.0"] = SCORING_MANIFEST_SCHEMA
    run_id: NonEmptyStr
    scoring_id: NonEmptyStr
    scorer_version: NonEmptyStr
    metric_definitions_version: NonEmptyStr
    gold_derivation_version: NonEmptyStr
    view_manifest_sha256: Sha256Hex
    bench_manifest_sha256: Sha256Hex
    gold_manifest_sha256: Sha256Hex
    registries_sha256: Sha256Hex
    rubric_sha256: Sha256Hex
    profile_sha256: Sha256Hex
    human_confirmations_sha256: Sha256Hex | None = None
    sample_seed: int
    created_at: AwareDatetime
    outputs: dict[str, Sha256Hex]


class RunCompletion(Contract):
    run_id: NonEmptyStr
    status: Literal["completed", "failed", "abandoned"]
    finished_at: AwareDatetime
    n_executions_expected: int = Field(ge=0)
    n_records_written: int = Field(ge=0)
    n_evaluation_failed: int = Field(ge=0)
    n_executions_with_errors: int = Field(ge=0)
    verified_after_run: bool
    failure_reason: str | None = None
