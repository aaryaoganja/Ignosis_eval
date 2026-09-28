"""Load a run for scoring — verification first, fail closed.

Order of operations (any failure raises ScoringError / IntegrityError and nothing is scored):
  1. run manifest present and schema-valid; completion record present with status 'completed'
     (unless allow_incomplete);
  2. benchmark manifest hash == the one recorded in the run manifest, and every benchmark file hash
     re-verified; dataset and split hashes equal the recorded ones;
  3. gold manifest hash == the recorded one, every gold file re-verified, gold/split hashes equal;
  4. profile file hash == the recorded one;
  5. every run item exists in the benchmark split and has frozen gold.
Only then are evaluator artifacts (records, normalized inputs) read — as data, never trusted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from ignosis_eval.contracts.benchmark import BenchmarkManifest, CaseMetadata
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.contracts.io import read_json
from ignosis_eval.contracts.profile import Profile, load_profile
from ignosis_eval.contracts.run_manifest import (
    COMPLETION_FILE,
    ERRORS_FILE,
    MANIFEST_FILE,
    NORMALIZED_INPUT_FILE,
    RECORD_FILE,
    RunCompletion,
    RunManifest,
    rep_dir,
)
from ignosis_eval.integrity.freeze import load_frozen_gold, manifest_file_sha256, verify_benchmark, verify_gold
from ignosis_eval.metrics.alignment import RawOutput


class ScoringError(RuntimeError):
    """Fail-closed scoring precondition failure."""


@dataclass
class ScoringInputs:
    run_dir: Path
    manifest: RunManifest
    manifest_sha256: str
    completion: RunCompletion | None
    benchmark: BenchmarkManifest
    benchmark_sha256: str
    gold_manifest_sha256: str
    gold: dict[str, GoldLabel]
    cases: dict[str, CaseMetadata]
    profile: Profile
    profile_sha256: str
    outputs: dict[tuple[str, int], RawOutput] = field(default_factory=dict)


def _read_optional_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"__unparseable__": str(exc)}


def load_scoring_inputs(
    run_dir: str | Path, benchmark_root: str | Path, *, profile_path: str | Path | None = None,
    allow_incomplete: bool = False,
) -> ScoringInputs:
    run_dir = Path(run_dir)
    mpath = run_dir / MANIFEST_FILE
    if not mpath.exists():
        raise ScoringError(f"run manifest missing: {mpath}")
    try:
        manifest = RunManifest.model_validate(read_json(mpath))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise ScoringError(f"run manifest invalid or missing required metadata: {exc}") from exc
    msha = manifest_file_sha256(mpath)

    completion = None
    cpath = run_dir / COMPLETION_FILE
    if cpath.exists():
        completion = RunCompletion.model_validate(read_json(cpath))
    if not allow_incomplete:
        if completion is None:
            raise ScoringError("run has no completion record (still running or crashed); refusing to score")
        if completion.status != "completed":
            raise ScoringError(f"run status is {completion.status!r}: {completion.failure_reason}")

    bm, bsha = verify_benchmark(benchmark_root, manifest.dataset.benchmark_manifest_sha256)
    ds = manifest.dataset
    if bm.dataset_hash != ds.dataset_hash or bm.split_hashes[ds.split] != ds.split_hash:
        raise ScoringError("benchmark dataset/split hash differs from the run manifest")
    if (bm.dataset_name, bm.dataset_version) != (ds.name, ds.version):
        raise ScoringError("benchmark name/version differs from the run manifest")

    if manifest.gold is None:
        raise ScoringError("run manifest has no gold reference; scoring requires frozen gold")
    gm, gsha = verify_gold(benchmark_root, expected_gold_manifest_sha256=manifest.gold.gold_manifest_sha256,
                           expected_benchmark_manifest_sha256=bsha)
    if gm.gold_hash != manifest.gold.gold_hash or gm.split_hashes[ds.split] != manifest.gold.split_hash:
        raise ScoringError("gold hash differs from the run manifest")
    gold = load_frozen_gold(benchmark_root, gm)

    ppath = Path(profile_path or manifest.profile.path)
    if not ppath.exists():
        raise ScoringError(f"profile file not found: {ppath}")
    profile, psha = load_profile(ppath)
    if psha != manifest.profile.sha256:
        raise ScoringError(f"profile hash {psha} != run manifest profile hash {manifest.profile.sha256}")

    cases: dict[str, CaseMetadata] = {}
    for item_id in ds.item_ids:
        entry = bm.case(item_id)
        if entry is None or entry.metadata.split is not ds.split:
            raise ScoringError(f"run item {item_id} is not in benchmark split {ds.split}")
        if item_id not in gold:
            raise ScoringError(f"run item {item_id} has no frozen gold")
        if gold[item_id].split is not ds.split:
            raise ScoringError(f"gold for {item_id} is in split {gold[item_id].split}, run split is {ds.split}")
        cases[item_id] = entry.metadata

    outputs: dict[tuple[str, int], RawOutput] = {}
    for item_id in ds.item_ids:
        for rep in range(1, manifest.repetitions + 1):
            d = rep_dir(run_dir, item_id, rep)
            errors = _read_optional_json(d / ERRORS_FILE)
            outputs[(item_id, rep)] = RawOutput(
                record_json=_read_optional_json(d / RECORD_FILE),
                normalized_input_json=_read_optional_json(d / NORMALIZED_INPUT_FILE),
                errors=errors if isinstance(errors, list) else ([] if errors is None else [errors]),
            )
    return ScoringInputs(run_dir, manifest, msha, completion, bm, bsha, gsha, gold, cases, profile, psha, outputs)
