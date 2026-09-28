"""Load a blinded run for scoring — verification first, fail closed (P-8, P-10, P-12).

  1. the view manifest is schema-valid and every listed view file re-hashes to its recorded hash;
  2. the bench manifest (dev or private hash list) and every item file re-verify, with the manifest hash the
     run recorded; same for the gold manifest and every gold file;
  3. rubric / profile hashes equal the ones the run recorded; the registries hash too;
  4. every run unit exists in the bench manifest and has frozen gold (calibration items never scored).
Only then are records read — as data, never trusted: a record that fails schema validation or carries an
unknown code is an EVALUATION_FAILED rep (SD-02).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ignosis_eval.benchmark.layout import BenchLayout
from ignosis_eval.contracts.benchmark import BenchManifest, UnitFacts
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.evaluation_record import EvaluationRecord
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.contracts.io import read_json
from ignosis_eval.contracts.record_checks import schema_errors
from ignosis_eval.contracts.registries import Registries
from ignosis_eval.contracts.run_manifest import (
    NORMALIZED_INPUT_FILE,
    RECORD_FILE,
    TIMING_FILE,
    USAGE_FILE,
    VIEW_MANIFEST_FILE,
    BlindViewManifest,
    rep_dir,
)
from ignosis_eval.integrity.freeze import IntegrityError, load_frozen_gold, manifest_file_sha256, verify_bench, verify_gold
from ignosis_eval.integrity.hashing import file_canonical_sha256, hash_file, tree_hash
from ignosis_eval.metrics.alignment import RepObs, observe
from ignosis_eval.spec.loader import Spec


class ScoringError(RuntimeError):
    """Fail-closed scoring precondition failure."""


@dataclass
class ScoringInputs:
    view_dir: Path
    view: BlindViewManifest
    view_sha256: str
    bench: BenchManifest
    gold_manifest_sha256: str
    gold: dict[str, GoldLabel]
    registries: Registries
    facts: dict[str, UnitFacts] = field(default_factory=dict)  # unit_id -> facts
    reps: dict[tuple[str, str], list[RepObs]] = field(default_factory=dict)  # (alias, unit_id) -> reps
    nis: dict[tuple[str, str, int], NormalizedInput] = field(default_factory=dict)  # (alias, unit_id, rep)


def verify_view(view_dir: Path) -> tuple[BlindViewManifest, str]:
    path = view_dir / VIEW_MANIFEST_FILE
    if not path.exists():
        raise ScoringError(f"blinded view manifest missing: {path} (run `ignosis-eval blind` first)")
    try:
        view = BlindViewManifest.model_validate(read_json(path))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise ScoringError(f"view manifest invalid: {exc}") from exc
    problems = []
    listed = {f.path: f for f in view.files}
    on_disk = {p.relative_to(view_dir).as_posix() for p in view_dir.rglob("*") if p.is_file()} - {VIEW_MANIFEST_FILE}
    problems += [f"unlisted view file {x}" for x in sorted(on_disk - set(listed))]
    for rel, fh in sorted(listed.items()):
        p = view_dir / rel
        if not p.exists():
            problems.append(f"missing view file {rel}")
        elif hash_file(p, rel).sha256 != fh.sha256:
            problems.append(f"view file hash mismatch {rel}")
    if tree_hash((f.path, f.sha256) for f in view.files) != view.view_hash:
        problems.append("view_hash does not match the listed files")
    if problems:
        raise ScoringError("blinded view verification failed (fail closed):\n  " + "\n  ".join(problems))
    return view, manifest_file_sha256(path)


def _read(path: Path) -> Any:
    return read_json(path) if path.exists() else None


def load_for_scoring(view_dir: str | Path, layout: BenchLayout, spec: Spec) -> ScoringInputs:
    view_dir = Path(view_dir)
    view, vsha = verify_view(view_dir)
    ds, gr, sp = view.dataset, view.gold, view.spec
    try:
        bench, _ = verify_bench(layout, ds.scope, ds.bench_manifest_sha256)
        gman, gsha = verify_gold(layout, ds.scope, expected_gold_manifest_sha256=gr.gold_manifest_sha256,
                                 expected_bench_manifest_sha256=ds.bench_manifest_sha256)
    except IntegrityError as exc:
        raise ScoringError(str(exc)) from exc
    if gman.gold_hash != gr.gold_hash:
        raise ScoringError("gold hash differs from the one the run recorded")
    if spec.rubric_sha256 != sp.rubric_sha256 or spec.profile_sha256 != sp.profile_sha256:
        raise ScoringError("rubric / profile differ from the ones the run used (fail closed)")
    if not layout.registries_path.exists() and ds.registries_sha256:
        raise ScoringError("registries.json missing")
    if file_canonical_sha256(layout.registries_path) != ds.registries_sha256:
        raise ScoringError("registries.json differs from the one the run recorded (fail closed)")
    registries = Registries.model_validate(read_json(layout.registries_path))
    gold = load_frozen_gold(layout, ds.scope, gman)
    facts = {f.unit_id: f for e in bench.items for f in e.units}
    inp = ScoringInputs(view_dir, view, vsha, bench, gsha, gold, registries)
    for uid in ds.units:
        if uid not in facts:
            raise ScoringError(f"run unit {uid} is not in the bench manifest")
        f = facts[uid]
        entry = bench.item(f.item_id)
        assert entry is not None
        if entry.meta.scoring_role == "never":
            raise ScoringError(f"{f.item_id} is a calibration item and is never scored")
        if entry.meta.scoring_role == "scored" and f.item_id not in gold:
            raise ScoringError(f"{f.item_id} has no frozen gold")
        inp.facts[uid] = f
    for alias in view.aliases:
        for uid in ds.units:
            f = inp.facts[uid]
            reps = []
            for r in range(1, view.repetitions + 1):
                d = rep_dir(view_dir, alias, f.item_id, f.unit_mode.value, r)
                raw, timing, usage = _read(d / RECORD_FILE), _read(d / TIMING_FILE) or {}, _read(d / USAGE_FILE) or {}
                rec, err = None, None
                if raw is None:
                    err = "record missing"
                else:
                    try:
                        rec = EvaluationRecord.model_validate(raw)
                        errs = schema_errors(rec, spec.registry)
                        if errs:
                            err = "; ".join(errs)
                    except ValidationError as exc:
                        err = f"schema-invalid record: {exc.errors()[:2]}"
                reps.append(observe(rec, r, schema_error=err, latency_s=timing.get("latency_s"), usage=usage))
                ni_raw = _read(d / NORMALIZED_INPUT_FILE)
                if ni_raw is not None:
                    inp.nis[(alias, uid, r)] = NormalizedInput.model_validate(ni_raw)
            inp.reps[(alias, uid)] = reps
    return inp
