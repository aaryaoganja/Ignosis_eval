"""Benchmark manifest, gold freeze and fail-closed verification.

Lifecycle
---------
1. Author cases + cards; `ignosis-eval benchmark check` passes.
2. `ignosis-eval manifest build`  -> manifests/benchmark_manifest.json (dataset version + hashes).
3. Label gold (separately, blind as required); `benchmark check --require-gold` passes.
4. `ignosis-eval gold freeze`     -> canonicalizes gold files, writes manifests/gold_manifest.json,
                                     marks gold files read-only.
5. Every scoring run calls `verify_benchmark` + `verify_gold` and compares the hashes with the ones
   recorded in the run manifest. Any mismatch, missing file, extra file or missing required metadata
   raises IntegrityError: the experiment fails closed.

Versions are immutable: re-using a dataset_version / gold_version for different content is refused.
Each written manifest is also archived write-once under manifests/history/.
"""

from __future__ import annotations

import json
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from ignosis_eval.benchmark.case_card_rules import load_case_card_file
from ignosis_eval.benchmark.checks import check_benchmark
from ignosis_eval.benchmark.layout import BenchmarkLayout, content_fingerprint
from ignosis_eval.contracts.benchmark import (
    BenchmarkManifest,
    CaseEntry,
    GoldManifest,
    GoldManifestEntry,
)
from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.case_card import CardStatus
from ignosis_eval.contracts.enums import Split
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.contracts.io import model_to_pretty_json, read_json
from ignosis_eval.contracts.profile import Profile
from ignosis_eval.integrity.hashing import canonicalize_json_file, file_canonical_sha256, hash_file, tree_hash


class IntegrityError(RuntimeError):
    """Fail-closed integrity violation (hash drift, missing file, missing metadata, version reuse)."""


# ------------------------------------------------------------------------------------ permissions
def set_read_only(path: Path) -> None:
    """Remove write permission from a file or a directory tree (files 0444, dirs 0555).

    NOTE: POSIX permissions do not bind root. The gold-access guard (integrity/guard.py) is the
    enforcement mechanism for the evaluator process; read-only bits are defence in depth.
    """
    path = Path(path)
    if not path.exists():
        return
    items = [path] if path.is_file() else [*sorted(path.rglob("*"), reverse=True), path]
    for p in items:
        mode = p.stat().st_mode
        p.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def make_writable(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        return
    items = [path] if path.is_file() else [path, *path.rglob("*")]
    for p in items:
        p.chmod(p.stat().st_mode | stat.S_IWUSR)


def is_read_only(path: Path) -> bool:
    path = Path(path)
    items = [path] if path.is_file() else [path, *path.rglob("*")]
    return all(not (p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)) for p in items)


def _write_manifest(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        make_writable(path)
    path.write_text(content, encoding="utf-8")
    set_read_only(path)


def _archive(layout: BenchmarkLayout, kind: str, version: str, content: str) -> None:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", version)
    dst = layout.manifests_dir / "history" / f"{kind}__{safe}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if dst.read_text(encoding="utf-8") != content:
            raise IntegrityError(f"history entry {dst.name} exists with different content")
        return
    with open(dst, "x", encoding="utf-8") as fh:  # write-once
        fh.write(content)
    set_read_only(dst)


def manifest_file_sha256(path: Path) -> str:
    if not path.exists():
        raise IntegrityError(f"manifest missing: {path}")
    return file_canonical_sha256(path)


# ------------------------------------------------------------------------------------ benchmark manifest
def _case_entries(layout: BenchmarkLayout) -> list[CaseEntry]:
    cases, _ = layout.discover_dataset()
    entries: list[CaseEntry] = []
    for cp in sorted(cases, key=lambda c: c.case_id):
        inp = CanonicalInput.model_validate(read_json(cp.input_path))
        card, _ = load_case_card_file(cp.card_path)
        if card is None:
            raise IntegrityError(f"{cp.case_id}: case card invalid or missing")
        files = [hash_file(p, layout.rel(p)) for p in sorted(cp.case_dir.iterdir()) if p.is_file()]
        files.append(hash_file(cp.card_path, layout.rel(cp.card_path)))
        entries.append(CaseEntry(
            metadata=card.to_metadata(),
            input_path=layout.rel(cp.input_path),
            case_card_path=layout.rel(cp.card_path),
            files=sorted(files, key=lambda f: f.path),
            content_fingerprint=content_fingerprint(inp),
        ))
    return entries


def _split_hashes(entries: list[CaseEntry]) -> dict[Split, str]:
    return {s: tree_hash((f.path, f.sha256) for e in entries if e.metadata.split is s for f in e.files) for s in Split}


def build_benchmark_manifest(
    root: str | Path, *, dataset_name: str, dataset_version: str, created_by: str,
    profile: Profile | None = None, notes: str | None = None, write: bool = True,
) -> BenchmarkManifest:
    layout = BenchmarkLayout(root)
    rep = check_benchmark(root, profile=profile)
    if not rep.ok:
        raise IntegrityError("refusing to build a manifest for a benchmark with errors:\n"
                             + "\n".join(str(i) for i in rep.errors))
    entries = _case_entries(layout)
    manifest = BenchmarkManifest(
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        created_at=datetime.now(timezone.utc),
        created_by=created_by,
        cases=entries,
        split_hashes=_split_hashes(entries),
        dataset_hash=tree_hash((f.path, f.sha256) for e in entries for f in e.files),
        notes=notes,
    )
    if not write:
        return manifest
    path = layout.benchmark_manifest_path
    if path.exists():
        try:
            old = BenchmarkManifest.model_validate(read_json(path))
        except (ValidationError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"existing benchmark manifest is invalid: {exc}") from exc
        if old.dataset_version == dataset_version:
            if old.dataset_hash != manifest.dataset_hash:
                raise IntegrityError(
                    f"dataset_version {dataset_version!r} already exists with different content; "
                    "bump dataset_version (versions are immutable)")
            return old  # idempotent
    content = model_to_pretty_json(manifest)
    _write_manifest(path, content)
    _archive(layout, "benchmark_manifest", dataset_version, content)
    return manifest


def load_benchmark_manifest(root: str | Path) -> tuple[BenchmarkManifest, str]:
    layout = BenchmarkLayout(root)
    path = layout.benchmark_manifest_path
    if not path.exists():
        raise IntegrityError(f"benchmark manifest missing: {path}")
    try:
        manifest = BenchmarkManifest.model_validate(read_json(path))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"benchmark manifest invalid or missing required metadata: {exc}") from exc
    return manifest, manifest_file_sha256(path)


def verify_benchmark(root: str | Path, expected_manifest_sha256: str | None = None) -> tuple[BenchmarkManifest, str]:
    """Recompute every hash and compare with the manifest. Raises IntegrityError on any drift."""
    layout = BenchmarkLayout(root)
    manifest, sha = load_benchmark_manifest(root)
    if expected_manifest_sha256 is not None and sha != expected_manifest_sha256:
        raise IntegrityError(f"benchmark manifest sha256 {sha} != expected {expected_manifest_sha256}")
    problems: list[str] = []
    listed = {e.metadata.case_id: e for e in manifest.cases}
    found, layout_problems = layout.discover_dataset()
    problems += layout_problems
    found_ids = {c.case_id: c for c in found}
    for cid in sorted(set(found_ids) - set(listed)):
        problems.append(f"case {cid} present on disk but not in manifest")
    for cid in sorted(set(listed) - set(found_ids)):
        problems.append(f"case {cid} in manifest but missing on disk")
    for cid, entry in sorted(listed.items()):
        cp = found_ids.get(cid)
        if cp is None:
            continue
        if cp.split is not entry.metadata.split:
            problems.append(f"case {cid}: on disk in {cp.split}, manifest says {entry.metadata.split}")
        on_disk = {layout.rel(p) for p in cp.case_dir.iterdir() if p.is_file()} | {layout.rel(cp.card_path)}
        listed_files = {f.path: f for f in entry.files}
        for extra in sorted(on_disk - set(listed_files)):
            problems.append(f"case {cid}: unlisted file {extra}")
        for fpath, fh in sorted(listed_files.items()):
            p = layout.root / fpath
            if not p.exists():
                problems.append(f"case {cid}: missing file {fpath}")
                continue
            try:
                actual = hash_file(p, fpath)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                problems.append(f"case {cid}: unreadable {fpath}: {exc}")
                continue
            if actual.sha256 != fh.sha256:
                problems.append(f"case {cid}: hash mismatch for {fpath}")
    recomputed_splits = _split_hashes(manifest.cases)
    if recomputed_splits != manifest.split_hashes:
        problems.append("split_hashes do not match the listed file hashes")
    if tree_hash((f.path, f.sha256) for e in manifest.cases for f in e.files) != manifest.dataset_hash:
        problems.append("dataset_hash does not match the listed file hashes")
    if problems:
        raise IntegrityError("benchmark verification failed (fail closed):\n  " + "\n  ".join(problems))
    return manifest, sha


# ------------------------------------------------------------------------------------ gold
def freeze_gold(
    root: str | Path, *, gold_version: str, frozen_by: str, labeling_protocol_version: str,
    profile: Profile | None = None,
) -> GoldManifest:
    layout = BenchmarkLayout(root)
    bmanifest, bsha = verify_benchmark(root)
    rep = check_benchmark(root, require_gold=True, profile=profile)
    if not rep.ok:
        raise IntegrityError("refusing to freeze gold: benchmark has errors:\n" + "\n".join(str(i) for i in rep.errors))
    problems: list[str] = []
    entries: list[GoldManifestEntry] = []
    for case in sorted(bmanifest.cases, key=lambda c: c.metadata.case_id):
        cid, split = case.metadata.case_id, case.metadata.split
        lc = rep.cases[cid]
        if lc.card is None or lc.card.status is not CardStatus.APPROVED:
            problems.append(f"{cid}: case card must be 'approved' before gold freeze")
        gpath = layout.case_paths(split, cid).gold_path
        gold = lc.gold
        if gold is None:
            problems.append(f"{cid}: gold missing")
            continue
        if gold.provenance.labeling_protocol_version != labeling_protocol_version:
            problems.append(f"{cid}: gold labeled under protocol {gold.provenance.labeling_protocol_version!r}, "
                            f"freezing under {labeling_protocol_version!r}")
        if gold.provenance.case_card_sha256 is not None:
            card_hash = next(f.sha256 for f in case.files if f.path == case.case_card_path)
            if card_hash != gold.provenance.case_card_sha256:
                problems.append(f"{cid}: case card changed after gold was labeled (case_card_sha256 mismatch)")
        entries.append(GoldManifestEntry(item_id=cid, split=split, path=layout.rel(gpath), sha256="0" * 64))
    if problems:
        raise IntegrityError("refusing to freeze gold:\n  " + "\n  ".join(problems))

    make_writable(layout.gold_dir)
    for e in entries:
        p = layout.root / e.path
        canonicalize_json_file(p)
        GoldLabel.model_validate(read_json(p))  # re-validate after canonicalization
        e.sha256 = file_canonical_sha256(p)
    manifest = GoldManifest(
        gold_version=gold_version,
        dataset_name=bmanifest.dataset_name,
        dataset_version=bmanifest.dataset_version,
        benchmark_manifest_sha256=bsha,
        labeling_protocol_version=labeling_protocol_version,
        frozen_at=datetime.now(timezone.utc),
        frozen_by=frozen_by,
        entries=entries,
        split_hashes={s: tree_hash((e.path, e.sha256) for e in entries if e.split is s) for s in Split},
        gold_hash=tree_hash((e.path, e.sha256) for e in entries),
    )
    path = layout.gold_manifest_path
    if path.exists():
        try:
            old = GoldManifest.model_validate(read_json(path))
        except (ValidationError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"existing gold manifest is invalid: {exc}") from exc
        if old.gold_version == gold_version:
            if old.gold_hash != manifest.gold_hash or old.benchmark_manifest_sha256 != bsha:
                set_read_only(layout.gold_dir)
                raise IntegrityError(f"gold_version {gold_version!r} already frozen with different content; "
                                     "bump gold_version (versions are immutable)")
            set_read_only(layout.gold_dir)
            return old
    content = model_to_pretty_json(manifest)
    _write_manifest(path, content)
    _archive(layout, "gold_manifest", gold_version, content)
    set_read_only(layout.gold_dir)
    return manifest


def load_gold_manifest(root: str | Path) -> tuple[GoldManifest, str]:
    layout = BenchmarkLayout(root)
    path = layout.gold_manifest_path
    if not path.exists():
        raise IntegrityError(f"gold manifest missing: {path} (gold has not been frozen)")
    try:
        manifest = GoldManifest.model_validate(read_json(path))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"gold manifest invalid or missing required metadata: {exc}") from exc
    return manifest, manifest_file_sha256(path)


def verify_gold(
    root: str | Path, *, expected_gold_manifest_sha256: str | None = None,
    expected_benchmark_manifest_sha256: str | None = None,
) -> tuple[GoldManifest, str]:
    layout = BenchmarkLayout(root)
    _, bsha = verify_benchmark(root, expected_benchmark_manifest_sha256)
    manifest, gsha = load_gold_manifest(root)
    problems: list[str] = []
    if expected_gold_manifest_sha256 is not None and gsha != expected_gold_manifest_sha256:
        problems.append(f"gold manifest sha256 {gsha} != expected {expected_gold_manifest_sha256}")
    if manifest.benchmark_manifest_sha256 != bsha:
        problems.append("gold manifest was frozen against a different benchmark manifest")
    listed = {e.path for e in manifest.entries}
    found, layout_problems = layout.discover_gold()
    problems += layout_problems
    on_disk = {layout.rel(p) for _, _, p in found}
    for extra in sorted(on_disk - listed):
        problems.append(f"unlisted gold file {extra}")
    for e in manifest.entries:
        p = layout.root / e.path
        if not p.exists():
            problems.append(f"gold file missing: {e.path}")
            continue
        try:
            actual = file_canonical_sha256(p)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            problems.append(f"gold file unreadable {e.path}: {exc}")
            continue
        if actual != e.sha256:
            problems.append(f"gold hash mismatch: {e.path}")
    if tree_hash((e.path, e.sha256) for e in manifest.entries) != manifest.gold_hash:
        problems.append("gold_hash does not match listed entries")
    if problems:
        raise IntegrityError("gold verification failed (fail closed):\n  " + "\n  ".join(problems))
    return manifest, gsha


def load_frozen_gold(root: str | Path, manifest: GoldManifest) -> dict[str, GoldLabel]:
    """Load gold labels listed in a VERIFIED gold manifest (call verify_gold first)."""
    layout = BenchmarkLayout(root)
    return {e.item_id: GoldLabel.model_validate(read_json(layout.root / e.path)) for e in manifest.entries}


__all__ = [
    "IntegrityError", "build_benchmark_manifest", "freeze_gold", "is_read_only", "load_benchmark_manifest",
    "load_frozen_gold", "load_gold_manifest", "make_writable", "set_read_only", "verify_benchmark", "verify_gold",
]
