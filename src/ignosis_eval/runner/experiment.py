"""Experiment runner — experiment-protocol P-2 … P-11.

Fail-closed sequence:
   1. load the spec pack (+ optional test profile), inventory PENDING_HUMAN_SIGNOFF items;
   2. verify the bench hash list and gold of the split's scope; run bench checks (gold required);
   3. locked runs: P-8 / H7 preflight (tag, clean tree, hashes, pending, confirm-holdout, registry);
   4. opaque unit aliases (P-17): a random alias per unit per run, the shared front end builds every
      NormalizedInput under its alias, the pre-run payload test fails the run on any identifier; alias mappings
      (P-10 systems, P-17 units) -> run manifest written once (locked: true for holdout / red team);
   5. K0 once per unit; then for r in 1..k: seeded shuffle of units (BASE_SEED + r), architecture order
      rotate([A, B], r − 1), A+ derived from A's stored raw output right after A (P-6);
   6. every (system, unit, rep) persists normalized input, raw LLM traffic, record, timing, usage, errors;
   7. a locked run must finish within 24 hours, else it is abandoned (P-6);
   8. re-verify hashes after the run; write completion; seal the run directory.

The evaluator runs inside ProtectedPathGuard (gold, case cards, manifests, registries). An evaluator that
touches them, an infrastructure error (e.g. a replay-fixture miss) or post-run hash drift fails the run.
Transport failure after 3 retries (P-3) yields an EVALUATION_FAILED record for that rep.
"""

from __future__ import annotations

import platform
import random
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pydantic

from ignosis_eval.benchmark.checks import check_bench
from ignosis_eval.benchmark.holdout import LockedRunEntry, register_locked_run
from ignosis_eval.benchmark.layout import BenchLayout, scope_of
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import Split, System, UnitMode
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, ExperimentMeta
from ignosis_eval.contracts.run_manifest import (
    COMPLETION_FILE,
    DEFAULT_REPETITIONS,
    DERIVATION_LOG_FILE,
    ERRORS_FILE,
    LLM_REQUESTS_FILE,
    LLM_RESPONSES_FILE,
    MANIFEST_FILE,
    NORMALIZED_INPUT_FILE,
    RECORD_FILE,
    TIMING_FILE,
    USAGE_FILE,
    ComponentRef,
    DatasetRef,
    GoldRef,
    HashVerification,
    PendingRef,
    RunCompletion,
    RunManifest,
    SpecRef,
    SystemConfig,
)
from ignosis_eval.evaluators.base import EvaluationContext, TraceSink
from ignosis_eval.evaluators.builder import failed_record
from ignosis_eval.evaluators.judgement import RuleEngine
from ignosis_eval.evaluators.llm import LLMUnavailableError
from ignosis_eval.evaluators.pipelines import APlusDeriver, a_final_raw_output
from ignosis_eval.evaluators.registry import build_systems
from ignosis_eval.integrity.freeze import IntegrityError, verify_bench, verify_gold
from ignosis_eval.integrity.guard import ProtectedPathGuard, ProtectedPathViolation
from ignosis_eval.integrity.hashing import file_canonical_sha256
from ignosis_eval.pipeline.asr import ASRAdapter
from ignosis_eval.pipeline.normalize import build_normalized_input
from ignosis_eval.runner.aliases import assign_unit_aliases, prerun_payload_check, write_unit_alias_mapping
from ignosis_eval.runner.blind import create_alias_mapping
from ignosis_eval.runner.gitinfo import git_info
from ignosis_eval.runner.lock import LOCKED_KINDS, preflight, verify_hashes
from ignosis_eval.runner.storage import ResultsLayout, RunStore, new_run_id, write_json_once, write_jsonl_once
from ignosis_eval.spec.loader import Spec, load_spec
from ignosis_eval.spec.pending import find_pending
from ignosis_eval.versions import (
    ENGINE_VERSION,
    FRONTEND_VERSION,
    GOLD_DERIVATION_VERSION,
    METRIC_DEFINITIONS_VERSION,
    PACKAGE_VERSION,
    PROMPT_TEMPLATE_VERSION,
    SCORER_VERSION,
)

ORDERING_ID = "P-6/v1: order_r = random.Random(BASE_SEED + r).shuffle(sorted unit ids); arch_order_r = " \
              "rotate([A, B], r - 1); A+ derived right after A; K0 once before rep 1"
LOCKED_MAX = timedelta(hours=24)


class RunConfigError(ValueError):
    pass


@dataclass
class RunConfig:
    bench_root: Path
    split: Split
    systems: list[System]
    kind: str = "dev"  # dev | dev_tuning | locked_holdout | locked_redteam
    base_seed: int = 0
    repetitions: int = DEFAULT_REPETITIONS
    results_root: Path = Path(".")
    private_root: Path | None = None
    spec_dir: Path | None = None
    profile_path: Path | None = None
    llm_backend: str = "mock_replay"
    replay_dir: Path | None = None
    model_id: str | None = None
    max_tokens: int | None = None
    tuning_round: int | None = None
    confirm_holdout: bool = False
    item_ids: list[str] | None = None
    purpose: str | None = None
    invocation: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    completion: RunCompletion


def seeded_order(unit_ids: list[str], base_seed: int, r: int) -> list[str]:
    order = sorted(unit_ids)
    random.Random(base_seed + r).shuffle(order)
    return order


def arch_order(systems: list[System], r: int) -> list[System]:
    base: list[System] = [s for s in (System.A, System.B) if s in systems]
    if not base:
        return []
    k = (r - 1) % len(base)
    return base[k:] + base[:k]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pending_refs(spec: Spec) -> list[PendingRef]:
    return [PendingRef(path=p.path, blocker=p.blocker, blocks_locked_run=p.blocks_locked_run)
            for p in find_pending(spec.rubric, spec.profile)]


def run_experiment(cfg: RunConfig, *, asr: ASRAdapter | None = None, rule_engine: RuleEngine | None = None,
                   systems_override: dict[System, Any] | None = None,
                   clock: Callable[[], datetime] = _now) -> RunResult:
    if cfg.kind not in ("dev", "dev_tuning", *LOCKED_KINDS):
        raise RunConfigError(f"unknown run kind {cfg.kind}")
    if cfg.repetitions < 1:
        raise RunConfigError("repetitions must be >= 1")
    if cfg.kind in LOCKED_KINDS and cfg.repetitions != DEFAULT_REPETITIONS:
        raise RunConfigError("locked runs use k = 5 (P-5)")
    if cfg.kind in LOCKED_KINDS and cfg.item_ids:
        raise RunConfigError("locked runs cover the whole split (no item subset)")
    if System.A_PLUS in cfg.systems and System.A not in cfg.systems:
        raise RunConfigError("A+ is derived from A: include A (P-4)")

    spec = load_spec(cfg.spec_dir, profile_path=cfg.profile_path)
    pending = _pending_refs(spec)
    layout = BenchLayout(cfg.bench_root, cfg.private_root)
    scope = scope_of(cfg.split)
    results = ResultsLayout(Path(cfg.results_root))
    bench, bsha = verify_bench(layout, scope)
    gman, gsha = verify_gold(layout, scope, expected_bench_manifest_sha256=bsha)
    report = check_bench(layout, spec, scopes=(scope,), require_gold=True)
    report.raise_if_errors()
    if not layout.registries_path.exists():
        raise IntegrityError(f"registries file missing: {layout.registries_path}")
    reg_sha = file_canonical_sha256(layout.registries_path)

    items = [e for e in bench.items if e.meta.split is cfg.split and e.meta.scoring_role == "scored"]
    if cfg.item_ids:
        unknown = set(cfg.item_ids) - {e.meta.item_id for e in items}
        if unknown:
            raise RunConfigError(f"items not in split {cfg.split.value}: {sorted(unknown)}")
        items = [e for e in items if e.meta.item_id in cfg.item_ids]
    units = [(e, f) for e in items for f in e.units]
    if not units:
        raise RunConfigError(f"split {cfg.split.value} has no scored units")
    if any(e.meta.item_id not in {g.item_id for g in gman.entries} for e, _ in units):
        raise IntegrityError("every run item needs frozen gold")

    systems: dict[System, Any] = systems_override or build_systems(cfg.systems, spec, llm_backend=cfg.llm_backend,
                                                replay_dir=cfg.replay_dir, model_id=cfg.model_id,
                                                max_tokens=cfg.max_tokens, rule_engine=rule_engine)
    configs: list[SystemConfig] = [systems[s].config() for s in cfg.systems]
    git = git_info()
    preflight(cfg.kind, split=cfg.split.value, confirm_holdout=cfg.confirm_holdout, git=git, spec=spec,
              pending=pending, systems=configs, results=results)
    if cfg.kind in LOCKED_KINDS:
        hv = verify_hashes(layout, scope, spec)
    else:
        hv = HashVerification(verified_at=clock(), bench_manifest=True, item_files=True,
                              private_hash_list=True if scope == "private" else None, gold=True, rubric=True,
                              profile=True, lexicons=True)

    # ------------------------------------------------------------------ shared front end, once per unit (P-2, P-17)
    run_id = new_run_id(cfg.kind, cfg.tuning_round)
    unit_aliases = assign_unit_aliases(f.unit_id for _, f in units)
    nis: dict[str, NormalizedInput] = {}
    own_tokens: dict[str, list[str]] = {}
    for e, f in units:
        ip = layout.item_paths(e.meta.split, e.meta.item_id)
        nis[f.unit_id] = build_normalized_input(e.meta, ip.item_dir, f.unit_mode, spec, asr,
                                                unit_alias=unit_aliases[f.unit_id])
        arts = e.meta.artifacts
        own_tokens[f.unit_id] = [e.meta.item_id, ip.item_dir.name] + \
            [Path(a).name for a in (arts.transcript, arts.audio, arts.platform_transcript) if a]
    p17_checked = prerun_payload_check(nis, own_tokens)  # P-17 rule 4: fails the run before any system call
    steps = sorted({f"{s.check}:{s.status}" for ni in nis.values() for s in ni.frontend.steps
                    if s.status in ("not_implemented", "pending_signoff")})
    facts = {f.unit_id: f for _, f in units}

    mapping_sha = create_alias_mapping(results, run_id, cfg.systems)
    unit_mapping_sha = write_unit_alias_mapping(
        results, run_id, unit_aliases, {f.unit_id: (f.item_id, f.unit_mode.value) for _, f in units},
        cfg.private_root)
    needs_audio = any(f.unit_mode not in (UnitMode.TRANSCRIPT, UnitMode.T_GOLD) for _, f in units)
    manifest = RunManifest(
        run_id=run_id, kind=cfg.kind, locked=cfg.kind in LOCKED_KINDS, tuning_round=cfg.tuning_round,  # type: ignore[arg-type]
        created_at=clock(), purpose=cfg.purpose,
        dataset=DatasetRef(name=bench.dataset_name, version=bench.dataset_version, split=cfg.split, scope=scope,  # type: ignore[arg-type]
                           bench_manifest_sha256=bsha, dataset_hash=bench.dataset_hash, registries_sha256=reg_sha,
                           units=[f.unit_id for _, f in units]),
        gold=GoldRef(gold_version=gman.gold_version, gold_manifest_sha256=gsha, gold_hash=gman.gold_hash),
        spec=SpecRef(contract_version=spec.contract_version, rubric_version=spec.rubric_version,
                     rubric_sha256=spec.rubric_sha256, profile_id=spec.profile_id,
                     profile_version=spec.profile_version, profile_sha256=spec.profile_sha256,
                     profile_path=str(spec.profile_path), profile_is_canonical=spec.is_canonical_profile,
                     lexicons_sha256=spec.lexicons_sha256, spec_file_sha256=dict(spec.file_sha256)),
        systems=configs,
        asr=ComponentRef(status="mock", **{k: v for k, v in asr.describe().items() if k != "model"})
        if (asr and needs_audio) else ComponentRef(status="pending_signoff" if needs_audio else "not_used",
                                                   blocker="B-06" if needs_audio else None),
        diarization=ComponentRef(status="pending_signoff" if needs_audio else "not_used",
                                 blocker="B-06" if needs_audio else None),
        telephony_simulation=ComponentRef(status="pending_signoff" if needs_audio else "not_used",
                                          blocker="B-09" if needs_audio else None),
        price_snapshot=ComponentRef(status="pending_signoff", blocker="B-08"),
        base_seed=cfg.base_seed, repetitions=cfg.repetitions, ordering=ORDERING_ID,
        alias_mapping_sha256=mapping_sha, unit_alias_mapping_sha256=unit_mapping_sha,
        p17_payload_strings_checked=p17_checked, git=git, hash_verification=hv, pending_signoff=pending,
        frontend_steps_not_implemented=steps,
        component_versions={"package": PACKAGE_VERSION, "frontend": FRONTEND_VERSION, "engine": ENGINE_VERSION,
                            "scorer": SCORER_VERSION, "metric_definitions": METRIC_DEFINITIONS_VERSION,
                            "gold_derivation": GOLD_DERIVATION_VERSION, "prompt_template": PROMPT_TEMPLATE_VERSION},
        environment={"python": platform.python_version(), "platform": platform.platform(),
                     "pydantic": pydantic.VERSION},
        invocation=list(cfg.invocation))

    store = RunStore(results.runs)
    run_dir = store.create_run(run_id)
    write_json_once(run_dir / MANIFEST_FILE, manifest.model_dump(mode="json"))
    if manifest.locked:
        assert git.tag is not None
        register_locked_run(results.locked_runs, LockedRunEntry(
            run_id=run_id, kind=cfg.kind, evaluator_tag=git.tag, commit=git.commit,  # type: ignore[arg-type]
            run_manifest_sha256=file_canonical_sha256(run_dir / MANIFEST_FILE), registered_at=clock()))
    store.append_ledger({"event": "start", "run_id": run_id, "at": manifest.created_at.isoformat(),
                         "kind": cfg.kind, "split": cfg.split.value, "systems": [s.value for s in cfg.systems]})

    started = clock()
    counters = {"records": 0, "failed": 0, "errors": 0}
    failure: str | None = None
    status = "completed"
    protected = layout.protected_paths() + [results.root]  # systems never read results (records, mappings, views)

    def execute(system: System, unit_id: str, rep: int) -> None:
        nonlocal failure
        f = facts[unit_id]
        ni = nis[unit_id]
        d = store.create_rep_dir(run_dir, system.value, f.item_id, f.unit_mode.value, rep)
        write_json_once(d / NORMALIZED_INPUT_FILE, ni.to_json_dict())
        trace, errors = TraceSink(), []
        t_start = time.perf_counter()
        t0 = clock()
        record: EvaluationRecord | None = None
        try:
            with ProtectedPathGuard(protected):
                record = systems[system].evaluate(ni, EvaluationContext(repetition=rep, spec=spec, trace=trace))
            record = EvaluationRecord.model_validate(record.model_dump(mode="json"))
        except LLMUnavailableError as exc:  # P-3: transport retries exhausted
            errors.append({"stage": "llm_transport", "type": type(exc).__name__, "message": str(exc)})
            record = failed_record(ni, spec, systems[system].system_info(), f"transport failure: {exc}", 0)
        except ProtectedPathViolation as exc:
            errors.append({"stage": "evaluate", "type": type(exc).__name__, "message": str(exc)})
            failure = f"{system.value} attempted a forbidden operation on {unit_id} rep {rep}: {exc}"
        except Exception as exc:  # infrastructure error: recorded, and the run fails closed
            errors.append({"stage": "evaluate", "type": type(exc).__name__, "message": str(exc),
                           "traceback": traceback.format_exc()})
            failure = f"{system.value} raised {type(exc).__name__} on {unit_id} rep {rep}: {exc}"
        if record is not None:
            record = record.model_copy(update={"experiment": ExperimentMeta(
                run_id=run_id, item_id=f.item_id, unit_mode=f.unit_mode, repetition=rep, base_seed=cfg.base_seed,
                started_at=t0, finished_at=clock())})
            write_json_once(d / RECORD_FILE, record.model_dump(mode="json"))
            counters["records"] += 1
            counters["failed"] += record.record_status.value == "EVALUATION_FAILED"
        latency = time.perf_counter() - t_start  # t_end = record persisted (SD-24)
        write_jsonl_once(d / LLM_REQUESTS_FILE, trace.requests)
        write_jsonl_once(d / LLM_RESPONSES_FILE, trace.responses)
        write_json_once(d / TIMING_FILE, {"latency_s": round(latency, 6), "started_at": t0.isoformat()})
        write_json_once(d / USAGE_FILE, trace.usage)
        write_json_once(d / ERRORS_FILE, errors)
        counters["errors"] += bool(errors)
        if system is System.A and System.A_PLUS in cfg.systems and failure is None:
            derive_aplus(unit_id, rep, record, trace)

    def derive_aplus(unit_id: str, rep: int, a_record: EvaluationRecord | None, a_trace: TraceSink) -> None:
        f = facts[unit_id]
        ni = nis[unit_id]
        d = store.create_rep_dir(run_dir, System.A_PLUS.value, f.item_id, f.unit_mode.value, rep)
        write_json_once(d / NORMALIZED_INPUT_FILE, ni.to_json_dict())
        deriver = systems[System.A_PLUS]
        assert isinstance(deriver, APlusDeriver)
        t = time.perf_counter()
        t0 = clock()
        rec, log = deriver.derive(a_record, a_final_raw_output(a_trace.responses), ni, spec)
        rec = rec.model_copy(update={"experiment": ExperimentMeta(
            run_id=run_id, item_id=f.item_id, unit_mode=f.unit_mode, repetition=rep, base_seed=cfg.base_seed,
            started_at=t0, finished_at=clock())})
        write_json_once(d / RECORD_FILE, rec.model_dump(mode="json"))
        derivation = time.perf_counter() - t
        write_json_once(d / DERIVATION_LOG_FILE, {"derived_from": "A", "rep": rep, "entries": log.entries})
        write_jsonl_once(d / LLM_REQUESTS_FILE, [])
        write_jsonl_once(d / LLM_RESPONSES_FILE, [])
        write_json_once(d / TIMING_FILE, {"derivation_s": round(derivation, 6), "started_at": t0.isoformat()})
        write_json_once(d / USAGE_FILE, TraceSink().usage)
        write_json_once(d / ERRORS_FILE, [])
        counters["records"] += 1
        counters["failed"] += rec.record_status.value == "EVALUATION_FAILED"

    def over_time() -> bool:
        return manifest.locked and clock() - started > LOCKED_MAX

    unit_ids = [f.unit_id for _, f in units]
    expected = 0
    if System.K0 in cfg.systems:  # K0: once per unit before rep 1
        expected += len(unit_ids)
        for uid in sorted(unit_ids):
            if failure or over_time():
                break
            execute(System.K0, uid, 1)
    llm_systems = arch_order(cfg.systems, 1)
    expected += len(unit_ids) * cfg.repetitions * (len(llm_systems) + (System.A_PLUS in cfg.systems))
    for r in range(1, cfg.repetitions + 1):
        if failure or over_time() or not llm_systems:
            break
        for uid in seeded_order(unit_ids, cfg.base_seed, r):
            for s in arch_order(cfg.systems, r):
                if failure or over_time():
                    break
                execute(s, uid, r)
    if over_time():
        status, failure = "abandoned", "locked run exceeded 24 hours (P-6): restart under a new run_id"

    try:
        verify_bench(layout, scope, bsha)
        verify_gold(layout, scope, expected_gold_manifest_sha256=gsha, expected_bench_manifest_sha256=bsha)
        verified = True
    except IntegrityError as exc:
        verified, failure = False, failure or f"hash drift during the run: {exc}"
    if failure and status == "completed":
        status = "failed"
    completion = RunCompletion(run_id=run_id, status=status, finished_at=clock(),  # type: ignore[arg-type]
                               n_executions_expected=expected, n_records_written=counters["records"],
                               n_evaluation_failed=counters["failed"], n_executions_with_errors=counters["errors"],
                               verified_after_run=verified, failure_reason=failure)
    write_json_once(run_dir / COMPLETION_FILE, completion.model_dump(mode="json"))
    store.append_ledger({"event": "finish", "run_id": run_id, "at": completion.finished_at.isoformat(),
                         "status": status, "failure_reason": failure})
    RunStore.seal(run_dir)
    if failure:
        raise IntegrityError(f"run {run_id} {status.upper()} (fail closed): {failure}")
    return RunResult(run_id, run_dir, completion)


__all__ = ["RunConfig", "RunConfigError", "RunResult", "arch_order", "run_experiment", "seeded_order"]
