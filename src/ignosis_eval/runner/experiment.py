"""Experiment runner: split x evaluator x repetitions -> append-only run + (optionally) scoring.

Fail-closed sequence
--------------------
 1. load profile (hash)                      6. write run manifest (before any evaluation)
 2. verify benchmark manifest + file hashes  7. mark gold read-only; evaluate every item x rep inside the
 3. verify gold manifest + gold hashes          gold-access guard, persisting all raw artifacts
 4. run benchmark checks (require gold)      8. re-verify benchmark + gold hashes after the run
 5. holdout / official-run preconditions     9. write completion record; seal the run; score

A guard violation (evaluator touching gold/case cards/manifests or spawning a process) or any post-run
hash drift marks the run FAILED; the scorer refuses failed runs.
"""

from __future__ import annotations

import hashlib
import platform
import random
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pydantic

from ignosis_eval.benchmark.checks import check_benchmark
from ignosis_eval.benchmark.layout import BenchmarkLayout
from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.enums import InputMode, Split
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, ExperimentMetadata
from ignosis_eval.contracts.io import read_json
from ignosis_eval.contracts.profile import load_profile
from ignosis_eval.contracts.run_manifest import (
    COMPLETION_FILE,
    ERRORS_FILE,
    LLM_REQUESTS_FILE,
    LLM_RESPONSES_FILE,
    MANIFEST_FILE,
    NORMALIZED_INPUT_FILE,
    RECORD_FILE,
    TIMING_FILE,
    USAGE_FILE,
    ASRInfo,
    AudioRenderingSummary,
    DatasetRef,
    GoldRef,
    ProfileRef,
    Randomization,
    RunCompletion,
    RunManifest,
)
from ignosis_eval.evaluators.base import EvaluationContext, TraceSink
from ignosis_eval.evaluators.registry import build_evaluator
from ignosis_eval.integrity.freeze import IntegrityError, set_read_only, verify_benchmark, verify_gold
from ignosis_eval.integrity.guard import ProtectedPathGuard, ProtectedPathViolation
from ignosis_eval.pipeline.asr import MockASR
from ignosis_eval.pipeline.normalize import normalize_input
from ignosis_eval.runner.gitinfo import git_info
from ignosis_eval.runner.storage import RunStore, new_run_id, write_json_once, write_jsonl_once
from ignosis_eval.scoring.scorer import ScoringConfig, score_run
from ignosis_eval.stats.proportion import IntervalPolicy
from ignosis_eval.versions import (
    CANONICAL_INPUT_SCHEMA,
    EVALUATION_RECORD_SCHEMA,
    GOLD_LABEL_SCHEMA,
    METRIC_DEFINITIONS_VERSION,
    PACKAGE_VERSION,
    RUN_MANIFEST_SCHEMA,
    SCORER_VERSION,
)

DEFAULT_PROFILE = Path("config/profiles/collections_placeholder.yaml")
REP_SEED_DERIVATION = "int(sha256(f'{seed}:{item_id}:{rep}').hexdigest()[:16], 16)"


class RunConfigError(ValueError):
    pass


@dataclass
class RunConfig:
    benchmark_root: Path
    split: Split
    evaluator: str
    repetitions: int = 1
    seed: int = 0
    runs_root: Path = Path("runs")
    scoring_root: Path = Path("scoring")
    profile_path: Path = DEFAULT_PROFILE
    llm_backend: str = "mock"
    model_id: str | None = None
    temperature: float = 0.0
    evaluator_options: dict[str, Any] = field(default_factory=dict)
    shuffle: bool = False
    official: bool = False
    confirm_holdout: bool = False
    purpose: str | None = None
    item_ids: list[str] | None = None
    score: bool = True
    interval_policy: IntervalPolicy = field(default_factory=IntervalPolicy)
    reference_language: str | None = None
    invocation: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    completion: RunCompletion
    scoring_dir: Path | None = None
    metrics: dict[str, Any] | None = None


def rep_seed(seed: int, item_id: str, rep: int) -> int:
    return int(hashlib.sha256(f"{seed}:{item_id}:{rep}".encode()).hexdigest()[:16], 16)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _post_run_verification(root: Path, bsha: str, gsha: str) -> str | None:
    try:
        verify_benchmark(root, bsha)
    except IntegrityError as exc:
        return f"benchmark changed during the run: {exc}"
    try:
        verify_gold(root, expected_gold_manifest_sha256=gsha, expected_benchmark_manifest_sha256=bsha)
    except IntegrityError as exc:
        return f"gold changed during the run: {exc}"
    return None


def run_experiment(cfg: RunConfig, *, evaluator=None) -> RunResult:
    """Execute one run. `evaluator` may be injected (tests); otherwise it is built from the config."""
    if cfg.repetitions < 1:
        raise RunConfigError("repetitions must be >= 1")
    if cfg.split is Split.HOLDOUT and not cfg.confirm_holdout:
        raise RunConfigError("holdout runs require --confirm-holdout (holdout must never be used for tuning)")
    if cfg.official and cfg.item_ids:
        raise RunConfigError("official runs must cover the whole split (no --items subset)")
    root = Path(cfg.benchmark_root).resolve()
    layout = BenchmarkLayout(root)

    profile, psha = load_profile(cfg.profile_path)
    bm, bsha = verify_benchmark(root)
    gm, gsha = verify_gold(root, expected_benchmark_manifest_sha256=bsha)
    report = check_benchmark(root, require_gold=True, profile=profile)
    report.raise_if_errors()

    cases = sorted(bm.split_cases(cfg.split), key=lambda c: c.metadata.case_id)
    if cfg.item_ids:
        wanted = set(cfg.item_ids)
        unknown = wanted - {c.metadata.case_id for c in cases}
        if unknown:
            raise RunConfigError(f"items not in split {cfg.split}: {sorted(unknown)}")
        cases = [c for c in cases if c.metadata.case_id in wanted]
    if not cases:
        raise RunConfigError(f"split {cfg.split} has no cases")
    missing_gold = [c.metadata.case_id for c in cases if gm.entry(c.metadata.case_id) is None]
    if missing_gold:
        raise IntegrityError(f"cases without frozen gold: {missing_gold}")

    if evaluator is None:
        evaluator = build_evaluator(cfg.evaluator, llm_backend=cfg.llm_backend, model_id=cfg.model_id,
                                    temperature=cfg.temperature, options=cfg.evaluator_options)
    erc = evaluator.run_config()
    git = git_info()
    if cfg.official and git.dirty:
        raise RunConfigError("official runs require a clean git working tree")
    if cfg.official and profile.status == "placeholder":
        raise RunConfigError("official runs cannot use a placeholder evaluation profile")

    inputs: dict[str, CanonicalInput] = {}
    for c in cases:
        inputs[c.metadata.case_id] = CanonicalInput.model_validate(read_json(root / c.input_path))
    needs_asr = any(i.input_mode is InputMode.AUDIO_ONLY for i in inputs.values())
    asr = MockASR() if needs_asr else None
    renderings = {hashlib.sha256(repr(sorted(i.audio.rendering.model_dump().items())).encode()).hexdigest():
                  i.audio.rendering.model_dump(mode="json")
                  for i in inputs.values() if i.audio is not None and i.audio.rendering is not None}

    order = [c.metadata.case_id for c in cases]
    if cfg.shuffle:
        random.Random(cfg.seed).shuffle(order)

    run_id = new_run_id(evaluator.name, cfg.split.value)
    manifest = RunManifest(
        run_id=run_id,
        created_at=_now(),
        purpose=cfg.purpose,
        official=cfg.official,
        dataset=DatasetRef(name=bm.dataset_name, version=bm.dataset_version, split=cfg.split,
                           benchmark_manifest_sha256=bsha, dataset_hash=bm.dataset_hash,
                           split_hash=bm.split_hashes[cfg.split], item_ids=order),
        gold=GoldRef(gold_version=gm.gold_version, gold_manifest_sha256=gsha, gold_hash=gm.gold_hash,
                     split_hash=gm.split_hashes[cfg.split]),
        rubric_version=profile.rubric_version,
        profile=ProfileRef(profile_id=profile.profile_id, profile_version=profile.profile_version,
                           rubric_version=profile.rubric_version, sha256=psha, path=str(cfg.profile_path),
                           status=profile.status),
        evaluator=erc,
        component_versions={"package": PACKAGE_VERSION, "scorer": SCORER_VERSION,
                            "metric_definitions": METRIC_DEFINITIONS_VERSION,
                            "canonical_input_schema": CANONICAL_INPUT_SCHEMA,
                            "evaluation_record_schema": EVALUATION_RECORD_SCHEMA,
                            "gold_label_schema": GOLD_LABEL_SCHEMA, "run_manifest_schema": RUN_MANIFEST_SCHEMA,
                            "evaluator": f"{erc.name}@{erc.version}"},
        git=git,
        asr=ASRInfo(**asr.describe()) if asr else None,
        audio_rendering=AudioRenderingSummary(
            n_items_with_audio=sum(i.audio is not None for i in inputs.values()),
            renderings=[renderings[k] for k in sorted(renderings)]),
        repetitions=cfg.repetitions,
        randomization=Randomization(seed=cfg.seed, item_order="shuffled" if cfg.shuffle else "manifest",
                                    rep_seed_derivation=REP_SEED_DERIVATION),
        environment={"python": platform.python_version(), "implementation": platform.python_implementation(),
                     "platform": platform.platform(), "pydantic": pydantic.VERSION},
        invocation=list(cfg.invocation),
    )

    store = RunStore(cfg.runs_root)
    run_dir = store.create_run(run_id)
    write_json_once(run_dir / MANIFEST_FILE, manifest.to_json_dict())
    store.append_ledger({"event": "start", "run_id": run_id, "at": manifest.created_at.isoformat(),
                         "split": cfg.split.value, "evaluator": erc.name, "official": cfg.official,
                         "dataset": f"{bm.dataset_name}@{bm.dataset_version}", "gold": gm.gold_version})
    set_read_only(layout.gold_dir)

    n_records = n_err_reps = 0
    failure: str | None = None
    for item_id in order:
        case_dir = (root / next(c for c in cases if c.metadata.case_id == item_id).input_path).parent
        for rep in range(1, cfg.repetitions + 1):
            d = store.create_rep_dir(run_dir, item_id, rep)
            errors: list[dict[str, Any]] = []
            trace = TraceSink()
            seed_r = rep_seed(cfg.seed, item_id, rep)
            started = _now()
            t0 = time.perf_counter()
            t_norm = t_eval = None
            stage = "normalize"
            try:
                with ProtectedPathGuard(layout.protected_paths):
                    normalized = normalize_input(inputs[item_id], case_dir, asr)
                t_norm = time.perf_counter()
                write_json_once(d / NORMALIZED_INPUT_FILE, normalized.to_json_dict())
                stage = "evaluate"
                ctx = EvaluationContext(repetition=rep, rep_seed=seed_r, profile=profile, profile_sha256=psha,
                                        trace=trace)
                with ProtectedPathGuard(layout.protected_paths):
                    record = evaluator.evaluate(normalized, ctx)
                t_eval = time.perf_counter()
                stage = "validate"
                record = EvaluationRecord.model_validate(record.model_dump(mode="json"))  # contract re-check
                record = record.model_copy(update={"experiment": ExperimentMetadata(
                    run_id=run_id, item_id=item_id, repetition=rep, seed=cfg.seed, rep_seed=seed_r,
                    started_at=started, finished_at=_now())})
                write_json_once(d / RECORD_FILE, record.to_json_dict())
                n_records += 1
            except ProtectedPathViolation as exc:
                errors.append({"stage": stage, "type": type(exc).__name__, "message": str(exc),
                               "traceback": traceback.format_exc()})
                failure = f"evaluator attempted a forbidden operation on {item_id} rep {rep}: {exc}"
            except Exception as exc:  # recorded, never swallowed silently
                errors.append({"stage": stage, "type": type(exc).__name__, "message": str(exc),
                               "traceback": traceback.format_exc()})
            finished = _now()
            write_jsonl_once(d / LLM_REQUESTS_FILE, trace.requests)
            write_jsonl_once(d / LLM_RESPONSES_FILE, trace.responses)
            write_json_once(d / TIMING_FILE, {
                "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                "wall_ms": round(1000 * (time.perf_counter() - t0), 3),
                "normalize_ms": round(1000 * (t_norm - t0), 3) if t_norm else None,
                "evaluate_ms": round(1000 * (t_eval - t_norm), 3) if (t_eval and t_norm) else None})
            write_json_once(d / USAGE_FILE, trace.usage)
            write_json_once(d / ERRORS_FILE, errors)
            n_err_reps += bool(errors)
            if failure:
                break
        if failure:
            break

    post = _post_run_verification(root, bsha, gsha)
    failure = failure or post
    completion = RunCompletion(
        run_id=run_id, status="failed" if failure else "completed", finished_at=_now(),
        n_item_reps_expected=len(order) * cfg.repetitions, n_records_written=n_records,
        n_item_reps_with_errors=n_err_reps, benchmark_verified_after_run=post is None or "benchmark" not in post,
        gold_verified_after_run=post is None, failure_reason=failure)
    write_json_once(run_dir / COMPLETION_FILE, completion.to_json_dict())
    store.append_ledger({"event": "finish", "run_id": run_id, "at": completion.finished_at.isoformat(),
                         "status": completion.status, "failure_reason": failure})
    RunStore.seal(run_dir)

    result = RunResult(run_id, run_dir, completion)
    if failure:
        raise IntegrityError(f"run {run_id} FAILED (fail closed): {failure}")
    if cfg.score:
        out_dir, scored = score_run(run_dir, root, scoring_root=cfg.scoring_root, profile_path=cfg.profile_path,
                                    config=ScoringConfig(cfg.interval_policy, cfg.reference_language))
        result.scoring_dir = out_dir
        result.metrics = read_json(out_dir / "metrics.json")
    return result
