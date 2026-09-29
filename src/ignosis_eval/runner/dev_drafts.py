"""DEV draft runs: K0, A, A+ and B on the DEV transcript drafts in bench/dev/transcripts, before the transcript freeze.

This is NOT an experiment-protocol run. The drafts are not hash-listed, have no gold, and are pending native human
review (`provenance.yaml`), so the protocol runner (runner/experiment.py) refuses them. A draft run reuses the
protocol's machinery where it applies:
  * the shared front end (intake, evaluability, pre-checks) on an in-memory TRANSCRIPT item per draft;
  * P-17: a random alias per unit per run, the pre-run identity-leak test, and the alias mapping written apart from
    what systems see;
  * the P-6 ordering (K0 once per unit before rep 1, seeded unit order per rep, A/B rotation, A+ derived from A's
    stored raw output right after A), the gold-access guard, write-once artifacts per (system, unit, rep);
  * authoring constraint 1: an LLM system whose model family equals the drafts' assisting family is refused.
Scored full calls only: the design's snippets (component tests of the normalizer, B-04) and the tuning-only audio
copy (G-02-N5, B-06 / B-09) are listed as excluded with the reason.

Output: <results_root>/dev_draft_runs/<run_id>/ with `draft_run.json` (configuration, versions, prompt hashes,
front-end results, exclusions) and `completion.json`. `devbaseline/` reads it. `consistency=True` runs K0 in every
rep as well, for the reproducibility smoke test.
"""

from __future__ import annotations

import hashlib
import platform
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pydantic
import yaml

from ignosis_eval.benchmark.layout import BenchLayout
from ignosis_eval.benchmark.public_dev import DevDesign, validate_public_dev
from ignosis_eval.contracts.benchmark import ItemArtifacts, ItemMeta
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import Split, System, UnitMode
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, ExperimentMeta
from ignosis_eval.contracts.run_manifest import (
    DERIVATION_LOG_FILE,
    ERRORS_FILE,
    LLM_REQUESTS_FILE,
    LLM_RESPONSES_FILE,
    NORMALIZED_INPUT_FILE,
    RECORD_FILE,
    TIMING_FILE,
    USAGE_FILE,
)
from ignosis_eval.evaluators.base import EvaluationContext, TraceSink, input_sha256
from ignosis_eval.evaluators.builder import failed_record
from ignosis_eval.evaluators.llm import (
    LLMUnavailableError,
    ProviderConfigError,
    ProviderRequestError,
    backend_family,
)
from ignosis_eval.evaluators.pipelines import APlusDeriver, a_final_raw_output
from ignosis_eval.evaluators.registry import build_systems
from ignosis_eval.integrity.guard import ProtectedPathGuard, ProtectedPathViolation
from ignosis_eval.pipeline.normalize import build_normalized_input
from ignosis_eval.runner.aliases import assign_unit_aliases, prerun_payload_check
from ignosis_eval.runner.experiment import arch_order, seeded_order
from ignosis_eval.runner.gitinfo import git_info
from ignosis_eval.runner.storage import write_json_once, write_jsonl_once
from ignosis_eval.spec.loader import Spec, load_spec
from ignosis_eval.spec.pending import find_pending
from ignosis_eval.versions import ENGINE_VERSION, FRONTEND_VERSION, PACKAGE_VERSION, PROMPT_TEMPLATE_VERSION

DRAFT_RUNS_DIR = "dev_draft_runs"
DRAFT_MANIFEST_FILE = "draft_run.json"
DRAFT_COMPLETION_FILE = "completion.json"
ALIAS_FILE = "unit_alias_mapping.json"
PROVENANCE_FILE = "provenance.yaml"
DRAFT_UNIT = UnitMode.TRANSCRIPT
NOTICE = ("DEV DRAFT RUN - not an experiment-protocol run. Transcripts are drafts pending native human review; there "
          "is no gold. Any comparison uses the frozen design intent (bench/public), not gold, and is not reliability "
          "evidence.")


class DraftRunError(RuntimeError):
    pass


@dataclass
class DraftRunConfig:
    drafts_dir: Path = Path("bench/dev/transcripts")
    bench_root: Path = Path("bench")
    systems: list[System] = field(default_factory=lambda: [System.K0])
    repetitions: int = 1
    base_seed: int = 0
    results_root: Path = Path(".")
    spec_dir: Path | None = None
    llm_backend: str = "mock_replay"
    model_id: str | None = None
    replay_dir: Path | None = None
    max_tokens: int | None = None
    item_ids: list[str] | None = None
    consistency: bool = False  # K0 in every rep (reproducibility smoke test)
    invocation: list[str] = field(default_factory=list)


@dataclass
class DraftRunResult:
    run_id: str
    run_dir: Path
    completion: dict[str, Any]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_provenance(drafts_dir: Path) -> dict[str, Any]:
    p = drafts_dir / PROVENANCE_FILE
    if not p.exists():
        raise DraftRunError(f"{p} missing: draft provenance is required (authoring constraint 1)")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise DraftRunError(f"{p} is not a provenance record")
    return data


def assisting_families(prov: dict[str, Any]) -> set[str]:
    return {str(e["llm_family"]) for e in prov["items"] if e.get("llm_assisted") and e.get("llm_family")}


def select_items(design: DevDesign, drafts_dir: Path, item_ids: list[str] | None
                 ) -> tuple[list[str], dict[str, str]]:
    """(scored full-call items with a draft, excluded item -> reason)."""
    run: list[str] = []
    excluded: dict[str, str] = {}
    for iid, it in sorted(design.items.items()):
        if item_ids is not None and iid not in item_ids:
            continue
        if it.gates is None:
            excluded[iid] = "snippet: a component test of the normalizer / extraction, not a call evaluation (B-04)"
        elif it.tuning_only:
            excluded[iid] = "tuning-only audio copy: its audio is rendered after B-06 / B-09; never scored"
        elif not (drafts_dir / f"{iid}.txt").exists():
            excluded[iid] = "no draft transcript"
        else:
            run.append(iid)
    unknown = set(item_ids or ()) - set(design.items)
    if unknown:
        raise DraftRunError(f"not DEV design items: {sorted(unknown)}")
    return run, excluded


def draft_meta(iid: str, design: DevDesign) -> ItemMeta:
    it = design.items[iid]
    return ItemMeta(item_id=iid, split=Split.DEV, pack=it.pack, language=it.language,  # type: ignore[arg-type]
                    unit_modes=[DRAFT_UNIT], artifacts=ItemArtifacts(transcript=f"{iid}.txt"), synthetic=True)


def _frontend_summary(ni: NormalizedInput) -> dict[str, Any]:
    fe = ni.frontend
    return {"evaluability": fe.evaluability_status.value, "reason_codes": [r.value for r in fe.reason_codes],
            "prechecks": {g.gate.value: g.status.value for g in fe.prechecks},
            "precheck_findings": [f.code for f in fe.precheck_findings],
            "steps_pending": sorted(f"{s.check}:{s.status}" for s in fe.steps
                                    if s.status in ("pending_signoff", "not_implemented")),
            "input_sha256": input_sha256(ni), "turns": len(ni.turns)}


def new_draft_run_id(now: datetime) -> str:
    return f"devdraft-{now.strftime('%Y%m%dT%H%M%S%fZ')}"


def run_dev_drafts(cfg: DraftRunConfig, *, systems_override: dict[System, Any] | None = None) -> DraftRunResult:
    if cfg.repetitions < 1:
        raise DraftRunError("repetitions must be >= 1")
    if System.A_PLUS in cfg.systems and System.A not in cfg.systems:
        raise DraftRunError("A+ is derived from A: include A (P-4)")
    spec: Spec = load_spec(cfg.spec_dir)
    layout = BenchLayout(cfg.bench_root)
    rep = validate_public_dev(layout.public_dir, spec)
    if rep.design is None or rep.errors:
        raise DraftRunError("the frozen DEV design does not validate (run `bench public-check`)")
    design = rep.design
    drafts = Path(cfg.drafts_dir)
    prov = load_provenance(drafts)
    llm = [s for s in cfg.systems if s in (System.A, System.B)]
    if llm:
        fam = backend_family(cfg.llm_backend)
        clash = fam in assisting_families(prov)
        if clash:
            raise DraftRunError(f"authoring constraint 1: the drafts are {sorted(assisting_families(prov))}-assisted "
                                f"and the evaluator backend {cfg.llm_backend!r} is the same family ({fam})")
    items, excluded = select_items(design, drafts, cfg.item_ids)
    if not items:
        raise DraftRunError("no draft items to run")
    try:
        systems: dict[System, Any] = systems_override or build_systems(cfg.systems, spec, llm_backend=cfg.llm_backend,
                                                    replay_dir=cfg.replay_dir, model_id=cfg.model_id,
                                                    max_tokens=cfg.max_tokens)
    except ProviderConfigError as exc:
        raise DraftRunError(f"PROVIDER NOT CONFIGURED: {exc}") from exc
    configs = [systems[s].config() for s in cfg.systems]

    # ------------------------------------------------------------------ front end once per unit (P-2, P-17)
    now = datetime.now(timezone.utc)
    run_id = new_draft_run_id(now)
    run_dir = Path(cfg.results_root) / DRAFT_RUNS_DIR / run_id
    if run_dir.exists():
        raise DraftRunError(f"run directory exists: {run_dir}")
    run_dir.mkdir(parents=True)
    aliases = assign_unit_aliases(items)
    nis: dict[str, NormalizedInput] = {}
    own: dict[str, list[str]] = {}
    for iid in items:
        nis[iid] = build_normalized_input(draft_meta(iid, design), drafts, DRAFT_UNIT, spec, unit_alias=aliases[iid])
        own[iid] = [iid, drafts.name, f"{iid}.txt"]
    checked = prerun_payload_check(nis, own)
    write_json_once(run_dir / ALIAS_FILE, {"run_id": run_id, "mapping": {aliases[i]: i for i in items}})
    llm_cfg = next((c for c in configs if c.system in (System.A, System.B)), None)
    manifest = {
        "kind": "dev_draft", "run_id": run_id, "created_at": now.isoformat(), "official": False, "notice": NOTICE,
        "drafts": {"dir": str(drafts), "files": {i: _sha(drafts / f"{i}.txt") for i in items},
                   "provenance_sha256": _sha(drafts / PROVENANCE_FILE),
                   "human_review_pending": bool(prov.get("human_review_pending", True)),
                   "assisting_families": sorted(assisting_families(prov))},
        "design": {"freeze_public_sha256": design.freeze_public_sha256},
        "items": {"executed": items, "excluded": excluded},
        "unit_mode": DRAFT_UNIT.value, "asr_mode": "none (transcript units only; ASR is B-06)",
        "spec": {"contract_version": spec.contract_version, "rubric_version": spec.rubric_version,
                 "rubric_sha256": spec.rubric_sha256, "profile_id": spec.profile_id,
                 "profile_version": spec.profile_version, "profile_sha256": spec.profile_sha256},
        "systems": [c.model_dump(mode="json") for c in configs],
        "llm": ({"backend": llm_cfg.llm_backend, "family": backend_family(llm_cfg.llm_backend),
                 "model_snapshot_id": llm_cfg.model_snapshot_id, "temperature": llm_cfg.temperature,
                 "seed": llm_cfg.seed, "max_tokens": llm_cfg.max_tokens,
                 "structured_output": llm_cfg.structured_output, "schema_retries": llm_cfg.schema_retries}
                if llm_cfg else None),
        "repetitions": cfg.repetitions, "base_seed": cfg.base_seed, "consistency_mode": cfg.consistency,
        "p17_payload_strings_checked": checked,
        "frontend": {i: _frontend_summary(nis[i]) for i in items},
        "pending_signoff": [{"path": p.path, "blocker": p.blocker} for p in find_pending(spec.rubric, spec.profile)],
        "component_versions": {"package": PACKAGE_VERSION, "frontend": FRONTEND_VERSION, "engine": ENGINE_VERSION,
                               "prompt_template": PROMPT_TEMPLATE_VERSION},
        "git": git_info().model_dump(mode="json"),
        "environment": {"python": platform.python_version(), "pydantic": pydantic.VERSION},
        "invocation": list(cfg.invocation),
    }
    write_json_once(run_dir / DRAFT_MANIFEST_FILE, manifest)

    protected = layout.protected_paths() + [drafts / PROVENANCE_FILE, Path(cfg.results_root) / DRAFT_RUNS_DIR]
    counters = {"records": 0, "failed": 0, "errors": 0}
    failure: str | None = None

    def rep_dir(system: System, iid: str, r: int) -> Path:
        d = run_dir / system.value / iid / DRAFT_UNIT.value / f"r{r}"
        d.mkdir(parents=True, exist_ok=False)
        return d

    def exp_meta(iid: str, r: int, t0: datetime) -> ExperimentMeta:
        return ExperimentMeta(run_id=run_id, item_id=iid, unit_mode=DRAFT_UNIT, repetition=r,
                              base_seed=cfg.base_seed, started_at=t0, finished_at=datetime.now(timezone.utc))

    def execute(system: System, iid: str, r: int) -> None:
        nonlocal failure
        ni = nis[iid]
        d = rep_dir(system, iid, r)
        write_json_once(d / NORMALIZED_INPUT_FILE, ni.to_json_dict())
        trace, errors = TraceSink(), list[dict[str, Any]]()
        t_start, t0 = time.perf_counter(), datetime.now(timezone.utc)
        record: EvaluationRecord | None = None
        try:
            with ProtectedPathGuard(protected):
                record = systems[system].evaluate(ni, EvaluationContext(repetition=r, spec=spec, trace=trace))
            record = EvaluationRecord.model_validate(record.model_dump(mode="json"))
        except LLMUnavailableError as exc:
            errors.append({"stage": "llm_transport", "type": type(exc).__name__, "message": str(exc)})
            record = failed_record(ni, spec, systems[system].system_info(), f"transport failure: {exc}", 0)
        except ProviderRequestError as exc:  # never a verdict: EVALUATION_FAILED; a config error stops the run
            errors.append({"stage": "llm_provider", "type": type(exc).__name__, "message": str(exc),
                           "http_status": exc.status})
            record = failed_record(ni, spec, systems[system].system_info(), f"provider error: {exc}", 0)
            if exc.is_config_error:
                failure = f"provider configuration error on {iid} rep {r}: {exc}"
        except ProtectedPathViolation as exc:
            errors.append({"stage": "evaluate", "type": type(exc).__name__, "message": str(exc)})
            failure = f"{system.value} attempted a forbidden operation on {iid} rep {r}: {exc}"
        except Exception as exc:  # infrastructure error: recorded; the run fails closed
            errors.append({"stage": "evaluate", "type": type(exc).__name__, "message": str(exc),
                           "traceback": traceback.format_exc()})
            failure = f"{system.value} raised {type(exc).__name__} on {iid} rep {r}: {exc}"
        if record is not None:
            record = record.model_copy(update={"experiment": exp_meta(iid, r, t0)})
            write_json_once(d / RECORD_FILE, record.model_dump(mode="json"))
            counters["records"] += 1
            counters["failed"] += record.record_status.value == "EVALUATION_FAILED"
        write_json_once(d / TIMING_FILE, {"latency_s": round(time.perf_counter() - t_start, 6),
                                          "started_at": t0.isoformat()})
        write_jsonl_once(d / LLM_REQUESTS_FILE, trace.requests)
        write_jsonl_once(d / LLM_RESPONSES_FILE, trace.responses)
        write_json_once(d / USAGE_FILE, trace.usage)
        write_json_once(d / ERRORS_FILE, errors)
        if trace.derivation:
            write_json_once(d / DERIVATION_LOG_FILE, {"system": system.value, "rep": r, "entries": trace.derivation})
        counters["errors"] += bool(errors)
        if system is System.A and System.A_PLUS in cfg.systems and failure is None:
            derive_aplus(iid, r, record, trace)

    def derive_aplus(iid: str, r: int, a_record: EvaluationRecord | None, a_trace: TraceSink) -> None:
        ni = nis[iid]
        d = rep_dir(System.A_PLUS, iid, r)
        write_json_once(d / NORMALIZED_INPUT_FILE, ni.to_json_dict())
        deriver = systems[System.A_PLUS]
        assert isinstance(deriver, APlusDeriver)
        t, t0 = time.perf_counter(), datetime.now(timezone.utc)
        rec, log = deriver.derive(a_record, a_final_raw_output(a_trace.responses), ni, spec)
        rec = rec.model_copy(update={"experiment": exp_meta(iid, r, t0)})
        write_json_once(d / RECORD_FILE, rec.model_dump(mode="json"))
        write_json_once(d / DERIVATION_LOG_FILE, {"derived_from": "A", "rep": r, "entries": log.entries})
        write_json_once(d / TIMING_FILE, {"derivation_s": round(time.perf_counter() - t, 6),
                                          "started_at": t0.isoformat()})
        write_jsonl_once(d / LLM_REQUESTS_FILE, [])
        write_jsonl_once(d / LLM_RESPONSES_FILE, [])
        write_json_once(d / USAGE_FILE, TraceSink().usage)
        write_json_once(d / ERRORS_FILE, [])
        counters["records"] += 1
        counters["failed"] += rec.record_status.value == "EVALUATION_FAILED"

    expected = 0
    if System.K0 in cfg.systems:
        k0_reps = range(1, cfg.repetitions + 1) if cfg.consistency else range(1, 2)
        for r in k0_reps:
            for iid in seeded_order(items, cfg.base_seed, r) if cfg.consistency else sorted(items):
                if failure:
                    break
                execute(System.K0, iid, r)
                expected += 1
    for r in range(1, cfg.repetitions + 1):
        if failure or not llm:
            break
        for iid in seeded_order(items, cfg.base_seed, r):
            for s in arch_order(cfg.systems, r):
                if failure:
                    break
                execute(s, iid, r)
                expected += 1 + (s is System.A and System.A_PLUS in cfg.systems)
    completion = {"run_id": run_id, "status": "failed" if failure else "completed",
                  "finished_at": datetime.now(timezone.utc).isoformat(), "n_records_written": counters["records"],
                  "n_executions_expected": expected, "n_evaluation_failed": counters["failed"],
                  "n_executions_with_errors": counters["errors"], "failure_reason": failure}
    write_json_once(run_dir / DRAFT_COMPLETION_FILE, completion)
    if failure:
        raise DraftRunError(f"draft run {run_id} FAILED (fail closed): {failure}")
    return DraftRunResult(run_id, run_dir, completion)


def frontend_stability(cfg: DraftRunConfig, reps: int) -> dict[str, int]:
    """Build every selected unit's normalized input `reps` times (fresh alias each time): item -> number of distinct
    input hashes (1 = the deterministic front end is stable)."""
    spec = load_spec(cfg.spec_dir)
    rep = validate_public_dev(BenchLayout(cfg.bench_root).public_dir, spec)
    if rep.design is None:
        raise DraftRunError("the frozen DEV design does not validate")
    items, _ = select_items(rep.design, Path(cfg.drafts_dir), cfg.item_ids)
    return {i: len({input_sha256(build_normalized_input(draft_meta(i, rep.design), Path(cfg.drafts_dir), DRAFT_UNIT,
                                                        spec)) for _ in range(reps)}) for i in items}


def evaluator_configuration(spec: Spec, llm_backend: str | None, model_id: str | None) -> dict[str, Any]:
    """The evaluator configuration a DEV baseline is (or would be) run with. Provider fields come from the central
    provider config (evaluators/provider_config.py); the key is never included, only whether it is configured."""
    from ignosis_eval.evaluators import provider_config
    from ignosis_eval.evaluators.k0 import KeywordFloorK0
    from ignosis_eval.evaluators.pipelines import EvaluatorA, EvaluatorB
    from ignosis_eval.evaluators.prompts import A_PROMPTS, B_PROMPTS, prompt_hashes

    pc = provider_config.describe()
    backend = llm_backend or provider_config.PROVIDER
    short = {k: v[:12] for k, v in {**prompt_hashes(A_PROMPTS, spec), **prompt_hashes(B_PROMPTS, spec)}.items()}
    if backend == provider_config.PROVIDER:
        provider = f"{provider_config.PROVIDER} ({pc['provider_label']})"
        key = ("configured (runtime env GEMINI_API_KEY)" if pc["api_key_configured"] else
               "MISSING: GEMINI_API_KEY is not set in the runtime environment; A / A+ / B cannot run")
        model = model_id or pc["model_id"]
    else:
        provider, key, model = backend, "n/a", model_id or "n/a"
    return {
        "provider": provider,
        "model": model,
        "API key": key,
        "model family rule": f"{pc['family']} must differ from the drafts' assisting family (anthropic-claude): "
                             "authoring constraint 1",
        "evaluator versions": {"K0": KeywordFloorK0.version, "A": EvaluatorA.version, "A+": APlusDeriver.version,
                               "B": EvaluatorB.version, "engine": ENGINE_VERSION, "frontend": FRONTEND_VERSION},
        "prompt version": f"{PROMPT_TEMPLATE_VERSION} (instruction text: UNOPTIMIZED STUB, not tuned)",
        "prompt hashes (sha256, 12)": short,
        "settings": {"temperature": pc["temperature"], "seed": pc["seed"], "max_output_tokens": pc["max_output_tokens"],
                     "timeout_s": pc["timeout_s"], "structured output": pc["structured_output"],
                     "transport retries": pc["transport_retry"], "consistency re-run": "disabled (R-05)"},
        "input modality": f"{pc['input_modality']} (ASR / diarization is B-06)",
        "locked-run pin": "B-05 stays PENDING_HUMAN_SIGNOFF for locked runs (spec-reconciliation §3.48)",
    }


__all__ = ["DRAFT_RUNS_DIR", "DraftRunConfig", "DraftRunError", "DraftRunResult", "NOTICE", "assisting_families",
           "draft_meta", "evaluator_configuration", "frontend_stability", "load_provenance", "run_dev_drafts",
           "select_items"]
