"""Blind scoring — experiment-protocol P-10.

1. At run start a seeded shuffle maps the run's systems to SYS-1..SYS-n. The seed is a fresh random value
   stored inside the mapping file (so the shuffle is reproducible from the file, but not from anything the
   scorer can read). The mapping file lives at <results>/blinding/<run_id>/alias_mapping.json, outside the
   scorer's input path, and its hash is written to the run manifest.
2. `build_view` materializes <results>/blinded/<run_id>/: records with the system replaced by its alias,
   normalized inputs, timing and usage, plus a hashed view manifest. K0's single run is replicated as reps
   1..k (P-4); A+ latency = A latency + derivation time (SD-24); A+ usage = A usage (SD-25).
3. `reveal` writes the mapping next to the score report only once the report exists and the caller supplies
   the report's hash (the committed hash, P-10 step 3).
"""

from __future__ import annotations

import json
import random
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ignosis_eval.canonical import canonical_json_pretty
from ignosis_eval.contracts.benchmark import FileHash
from ignosis_eval.contracts.enums import System
from ignosis_eval.contracts.io import read_json
from ignosis_eval.contracts.run_manifest import (
    ALIAS_MAPPING_FILE,
    COMPLETION_FILE,
    DERIVATION_LOG_FILE,
    MANIFEST_FILE,
    NORMALIZED_INPUT_FILE,
    RECORD_FILE,
    REVEAL_FILE,
    TIMING_FILE,
    USAGE_FILE,
    VIEW_MANIFEST_FILE,
    BlindViewManifest,
    H7Audit,
    RunCompletion,
    RunManifest,
    rep_dir,
)
from ignosis_eval.integrity.hashing import file_canonical_sha256, hash_file, tree_hash
from ignosis_eval.runner.storage import AppendOnlyError, ResultsLayout, write_json_once


class BlindingError(RuntimeError):
    pass


def create_alias_mapping(results: ResultsLayout, run_id: str, systems: list[System]) -> str:
    d = results.blinding / run_id
    d.mkdir(parents=True, exist_ok=False)
    seed = secrets.randbits(64)
    aliases = [f"SYS-{i}" for i in range(1, len(systems) + 1)]
    order = sorted(s.value for s in systems)
    random.Random(seed).shuffle(order)
    mapping = {"run_id": run_id, "seed": seed, "algorithm": "random.Random(seed).shuffle(sorted systems)",
               "mapping": dict(zip(order, aliases, strict=True))}
    path = d / ALIAS_MAPPING_FILE
    write_json_once(path, mapping)
    return file_canonical_sha256(path)


def _mapping(results: ResultsLayout, run_id: str, expected_sha: str) -> dict[str, str]:
    path = results.blinding / run_id / ALIAS_MAPPING_FILE
    if not path.exists() or file_canonical_sha256(path) != expected_sha:
        raise BlindingError("alias mapping missing or its hash differs from the run manifest (fail closed)")
    return read_json(path)["mapping"]


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def build_view(results: ResultsLayout, run_id: str) -> Path:
    run_dir = results.run_dir(run_id)
    manifest = RunManifest.model_validate(read_json(run_dir / MANIFEST_FILE))
    if not (run_dir / COMPLETION_FILE).exists():
        raise BlindingError(f"run {run_id} has no completion record")
    completion = RunCompletion.model_validate(read_json(run_dir / COMPLETION_FILE))
    if completion.status != "completed":
        raise BlindingError(f"run {run_id} is {completion.status}; only completed runs are scored (fail closed)")
    mapping = _mapping(results, run_id, manifest.alias_mapping_sha256)
    view_dir = results.blinded / run_id
    try:
        view_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise AppendOnlyError(f"blinded view already exists: {view_dir}") from exc
    systems = {s.system.value for s in manifest.systems}
    k = manifest.repetitions
    for sysname in sorted(systems):
        alias = mapping[sysname]
        for uid in manifest.dataset.units:
            item_id, mode = uid.split("__", 1)
            for r in range(1, k + 1):
                src_rep = 1 if sysname == System.K0.value else r
                src = rep_dir(run_dir, sysname, item_id, mode, src_rep)
                dst = rep_dir(view_dir, alias, item_id, mode, r)
                dst.mkdir(parents=True)
                record = read_json(src / RECORD_FILE)
                record["system"] = {"system": alias, "version": "blinded", "model_snapshot_id": None,
                                    "prompt_hashes": {}}
                (dst / RECORD_FILE).write_text(canonical_json_pretty(record), encoding="utf-8")
                _copy(src / NORMALIZED_INPUT_FILE, dst / NORMALIZED_INPUT_FILE)
                timing: dict[str, Any] = read_json(src / TIMING_FILE)
                usage: dict[str, Any] = read_json(src / USAGE_FILE)
                if sysname == System.A_PLUS.value:
                    a = rep_dir(run_dir, System.A.value, item_id, mode, r)
                    a_timing = read_json(a / TIMING_FILE)
                    timing = {"latency_s": round(a_timing["latency_s"] + timing["derivation_s"], 6),
                              "composition": "A latency + A+ derivation (SD-24)"}
                    usage = read_json(a / USAGE_FILE)  # SD-25: A+ = A
                    if not (src / DERIVATION_LOG_FILE).exists():
                        raise BlindingError(f"A+ derivation log missing for {uid} rep {r}")
                (dst / TIMING_FILE).write_text(canonical_json_pretty({"latency_s": timing.get("latency_s")}),
                                               encoding="utf-8")
                (dst / USAGE_FILE).write_text(canonical_json_pretty(usage), encoding="utf-8")
    files = [hash_file(p, p.relative_to(view_dir).as_posix()) for p in sorted(view_dir.rglob("*")) if p.is_file()]
    view = BlindViewManifest(
        run_id=run_id, run_manifest_sha256=file_canonical_sha256(run_dir / MANIFEST_FILE),
        alias_mapping_sha256=manifest.alias_mapping_sha256, aliases=sorted(mapping.values()),
        repetitions=k, base_seed=manifest.base_seed, dataset=manifest.dataset, gold=manifest.gold, spec=manifest.spec,
        model_snapshot_ids=sorted({s.model_snapshot_id for s in manifest.systems if s.model_snapshot_id}),
        mock_backend=any(s.llm_backend == "mock_replay" for s in manifest.systems),
        pending_signoff=manifest.pending_signoff,
        h7=H7Audit(kind=manifest.kind, locked=manifest.locked, git_tag=manifest.git.tag, git_dirty=manifest.git.dirty,
                   hash_verification=manifest.hash_verification, run_created_at=manifest.created_at),
        created_at=datetime.now(timezone.utc), files=[FileHash(**f.model_dump()) for f in files],
        view_hash=tree_hash((f.path, f.sha256) for f in files))
    write_json_once(view_dir / VIEW_MANIFEST_FILE, view.model_dump(mode="json"))
    for p in view_dir.rglob("*"):
        if p.is_file():
            p.chmod(0o444)
    return view_dir


def reveal(results: ResultsLayout, run_id: str, scoring_id: str, expected_report_sha256: str) -> Path:
    run_dir = results.run_dir(run_id)
    manifest = RunManifest.model_validate(read_json(run_dir / MANIFEST_FILE))
    sm_path = results.scoring / scoring_id / "scoring_manifest.json"
    if not sm_path.exists():
        raise BlindingError("the mapping is revealed only after the score report is written (P-10 step 3)")
    sha = file_canonical_sha256(sm_path)
    if sha != expected_report_sha256:
        raise BlindingError(f"score report hash {sha} != the committed hash supplied ({expected_report_sha256})")
    mapping = _mapping(results, run_id, manifest.alias_mapping_sha256)
    out = results.scoring / scoring_id / REVEAL_FILE
    write_json_once(out, {"run_id": run_id, "scoring_id": scoring_id, "score_report_sha256": sha,
                          "alias_mapping_sha256": manifest.alias_mapping_sha256,
                          "alias_to_system": {v: k for k, v in sorted(mapping.items())},
                          "revealed_at": datetime.now(timezone.utc).isoformat()})
    return out


__all__ = ["BlindingError", "build_view", "create_alias_mapping", "reveal", "json"]
