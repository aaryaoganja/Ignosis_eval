"""Bench manifests (hash lists), gold freeze and fail-closed verification — experiment-protocol P-1 / P-8.

Scopes: `dev` (bench/dev, in the repository) and `private` ($BENCH_PRIVATE_DIR: holdout, red team and their
gold). Each scope has a BenchManifest (every item file and case card, hashed) and a GoldManifest (every gold
file, hashed). All four manifests live in the repository (bench/manifests), so private files are hash-listed
in the repository (P-1 rule 5) and verified before every locked run (P-8 step 3).

Lifecycle:
  1. author items + cards (B-01); `ignosis-eval bench check` passes;
  2. `ignosis-eval bench manifest --scope dev|private`  -> hash list (versions are immutable);
  3. blind labeling (B-02); `bench check --require-gold` passes;
  4. `ignosis-eval gold freeze --scope ...` -> canonicalized, read-only gold + gold manifest;
  5. every run and every scoring re-verifies both manifests; any drift, missing or extra file, or missing
     metadata raises IntegrityError.

This module does not import evaluator code: unit facts are computed by an injected function (the CLI passes
the shared front end's `unit_facts`) and benchmark checks by an injected validator.
"""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from ignosis_eval.benchmark.layout import ITEM_META_FILE, SCOPE_SPLITS, BenchLayout
from ignosis_eval.canonical import canonical_sha256
from ignosis_eval.contracts.benchmark import (
    BenchManifest,
    GoldManifest,
    GoldManifestEntry,
    ItemEntry,
    ItemMeta,
    UnitFacts,
)
from ignosis_eval.contracts.case_card import CardStatus, CaseCard
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.contracts.io import model_to_pretty_json, read_json, read_yaml
from ignosis_eval.integrity.hashing import canonicalize_json_file, file_canonical_sha256, hash_file, tree_hash

FactsFn = Callable[[ItemMeta, Path], list[UnitFacts]]
Validator = Callable[[], list[str]]


class IntegrityError(RuntimeError):
    """Fail-closed integrity violation (hash drift, missing file, missing metadata, version reuse)."""


# ------------------------------------------------------------------------------------ permissions
def set_read_only(path: Path) -> None:
    """Files 0444 / dirs 0555. POSIX bits do not bind root: the guard and re-verification are the real
    controls; this is defence in depth."""
    path = Path(path)
    if not path.exists():
        return
    items = [path] if path.is_file() else [*sorted(path.rglob("*"), reverse=True), path]
    for p in items:
        p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


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


def _archive(layout: BenchLayout, kind: str, version: str, content: str) -> None:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", version)
    dst = layout.history_dir / f"{kind}__{safe}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if dst.read_text(encoding="utf-8") != content:
            raise IntegrityError(f"history entry {dst.name} exists with different content")
        return
    with open(dst, "x", encoding="utf-8") as fh:  # write-once
        fh.write(content)
    set_read_only(dst)


def manifest_file_sha256(path: Path) -> str:
    if not Path(path).exists():
        raise IntegrityError(f"manifest missing: {path}")
    return file_canonical_sha256(Path(path))


def content_fingerprint(meta: ItemMeta, item_dir: Path) -> str:
    """Hash of the item's modality content (transcript / audio / platform transcript bytes), ignoring ids and
    metadata. Identical content in two splits is leakage (P-1 rule 1)."""
    a = meta.artifacts
    parts = {}
    for name, rel in (("transcript", a.transcript), ("audio", a.audio), ("platform_transcript", a.platform_transcript)):
        p = item_dir / rel if rel else None
        parts[name] = hash_file(p, rel).sha256 if p is not None and p.exists() else None  # type: ignore[arg-type]
    return canonical_sha256(parts)


# ------------------------------------------------------------------------------------ bench manifests
def _item_entries(layout: BenchLayout, scope: str, facts_fn: FactsFn) -> list[ItemEntry]:
    entries: list[ItemEntry] = []
    for split in SCOPE_SPLITS[scope]:
        items, problems = layout.discover(split)
        if problems:
            raise IntegrityError("; ".join(problems))
        for ip in items:
            try:
                meta = ItemMeta.model_validate(read_json(ip.meta_path))
            except (ValidationError, json.JSONDecodeError, FileNotFoundError) as exc:
                raise IntegrityError(f"{ip.item_id}: {ITEM_META_FILE} invalid or missing: {exc}") from exc
            if meta.item_id != ip.item_id or meta.split is not split:
                raise IntegrityError(f"{ip.item_id}: item.json says {meta.item_id}/{meta.split}, directory says "
                                     f"{ip.item_id}/{split}")
            files = [hash_file(p, layout.rel(scope, p)) for p in sorted(ip.item_dir.rglob("*")) if p.is_file()]
            if not ip.card_path.exists():
                raise IntegrityError(f"{ip.item_id}: case card missing ({ip.card_path.name})")
            files.append(hash_file(ip.card_path, layout.rel(scope, ip.card_path)))
            entries.append(ItemEntry(meta=meta, files=sorted(files, key=lambda f: f.path),
                                     units=facts_fn(meta, ip.item_dir),
                                     content_fingerprint=content_fingerprint(meta, ip.item_dir)))
    return sorted(entries, key=lambda e: e.meta.item_id)


def build_bench_manifest(layout: BenchLayout, scope: str, *, dataset_name: str, dataset_version: str,
                         created_by: str, facts_fn: FactsFn, validator: Validator, notes: str | None = None,
                         write: bool = True) -> BenchManifest:
    errors = validator()
    if errors:
        raise IntegrityError("refusing to hash-list a benchmark with errors:\n  " + "\n  ".join(errors))
    entries = _item_entries(layout, scope, facts_fn)
    manifest = BenchManifest(dataset_name=dataset_name, dataset_version=dataset_version, scope=scope,  # type: ignore[arg-type]
                             created_at=datetime.now(timezone.utc), created_by=created_by, items=entries,
                             dataset_hash=tree_hash((f.path, f.sha256) for e in entries for f in e.files), notes=notes)
    if not write:
        return manifest
    path = layout.manifest_path(scope)
    if path.exists():
        old, _ = load_bench_manifest(layout, scope)
        if old.dataset_version == dataset_version:
            if old.dataset_hash != manifest.dataset_hash:
                raise IntegrityError(f"dataset_version {dataset_version!r} already exists with different content; "
                                     "bump the version (versions are immutable)")
            return old
    content = model_to_pretty_json(manifest)
    _write_manifest(path, content)
    _archive(layout, f"{scope}_manifest", dataset_version, content)
    return manifest


def load_bench_manifest(layout: BenchLayout, scope: str) -> tuple[BenchManifest, str]:
    path = layout.manifest_path(scope)
    if not path.exists():
        raise IntegrityError(f"{scope} bench manifest missing: {path}")
    try:
        m = BenchManifest.model_validate(read_json(path))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"{scope} bench manifest invalid or missing required metadata: {exc}") from exc
    if m.scope != scope:
        raise IntegrityError(f"{path.name} declares scope {m.scope}, expected {scope}")
    return m, manifest_file_sha256(path)


def verify_bench(layout: BenchLayout, scope: str, expected_sha256: str | None = None) -> tuple[BenchManifest, str]:
    """Re-hash every listed file; any drift, missing or unlisted file raises (fail closed)."""
    manifest, sha = load_bench_manifest(layout, scope)
    problems: list[str] = []
    if expected_sha256 is not None and sha != expected_sha256:
        problems.append(f"{scope} manifest sha256 {sha} != expected {expected_sha256}")
    listed = {e.meta.item_id: e for e in manifest.items}
    on_disk: dict[str, object] = {}
    for split in SCOPE_SPLITS[scope]:
        items, probs = layout.discover(split)
        problems += probs
        on_disk.update({ip.item_id: ip for ip in items})
    problems += [f"item {i} on disk but not hash-listed" for i in sorted(set(on_disk) - set(listed))]
    problems += [f"item {i} hash-listed but missing on disk" for i in sorted(set(listed) - set(on_disk))]
    for iid, e in sorted(listed.items()):
        ip = on_disk.get(iid)
        if ip is None:
            continue
        disk_files = {layout.rel(scope, p) for p in ip.item_dir.rglob("*") if p.is_file()}  # type: ignore[attr-defined]
        disk_files.add(layout.rel(scope, ip.card_path))  # type: ignore[attr-defined]
        lf = {f.path: f for f in e.files}
        problems += [f"{iid}: unlisted file {x}" for x in sorted(disk_files - set(lf))]
        for fp, fh in sorted(lf.items()):
            p = layout.scope_root(scope) / fp
            if not p.exists():
                problems.append(f"{iid}: missing file {fp}")
                continue
            try:
                if hash_file(p, fp).sha256 != fh.sha256:
                    problems.append(f"{iid}: hash mismatch {fp}")
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                problems.append(f"{iid}: unreadable {fp}: {exc}")
    if tree_hash((f.path, f.sha256) for e in manifest.items for f in e.files) != manifest.dataset_hash:
        problems.append("dataset_hash does not match the listed file hashes")
    if problems:
        raise IntegrityError(f"{scope} bench verification failed (fail closed):\n  " + "\n  ".join(problems))
    return manifest, sha


# ------------------------------------------------------------------------------------ gold
def freeze_gold(layout: BenchLayout, scope: str, *, gold_version: str, frozen_by: str,
                labeling_protocol_version: str, validator: Validator) -> GoldManifest:
    bman, bsha = verify_bench(layout, scope)
    errors = validator()
    if errors:
        raise IntegrityError("refusing to freeze gold: benchmark has errors:\n  " + "\n  ".join(errors))
    problems: list[str] = []
    entries: list[GoldManifestEntry] = []
    for item in bman.items:
        meta = item.meta
        if meta.scoring_role == "never":
            continue  # calibration items are never scored (§12.7)
        ip = layout.item_paths(meta.split, meta.item_id)
        try:
            card = CaseCard.model_validate(read_yaml(ip.card_path))
        except (ValidationError, OSError) as exc:
            problems.append(f"{meta.item_id}: case card invalid: {exc}")
            continue
        if card.status is not CardStatus.APPROVED:
            problems.append(f"{meta.item_id}: case card must be approved before gold freeze")
        if not ip.gold_path.exists():
            problems.append(f"{meta.item_id}: gold missing")
            continue
        try:
            gold = GoldLabel.model_validate(read_json(ip.gold_path))
        except (ValidationError, json.JSONDecodeError) as exc:
            problems.append(f"{meta.item_id}: gold invalid: {exc}")
            continue
        if gold.item_id != meta.item_id or gold.split is not meta.split:
            problems.append(f"{meta.item_id}: gold item/split mismatch")
        if gold.provenance.labeling_protocol_version != labeling_protocol_version:
            problems.append(f"{meta.item_id}: labeled under protocol {gold.provenance.labeling_protocol_version!r}")
        card_sha = next(f.sha256 for f in item.files if f.path == layout.rel(scope, ip.card_path))
        if gold.provenance.case_card_sha256 is not None and gold.provenance.case_card_sha256 != card_sha:
            problems.append(f"{meta.item_id}: case card changed after labeling (case_card_sha256 mismatch)")
        entries.append(GoldManifestEntry(item_id=meta.item_id, split=meta.split,
                                         path=layout.rel(scope, ip.gold_path), sha256="0" * 64))
    if problems:
        raise IntegrityError("refusing to freeze gold:\n  " + "\n  ".join(problems))
    gdir = layout.gold_dir(scope)
    make_writable(gdir)
    for e in entries:
        p = layout.scope_root(scope) / e.path
        canonicalize_json_file(p)
        GoldLabel.model_validate(read_json(p))
        e.sha256 = file_canonical_sha256(p)
    manifest = GoldManifest(gold_version=gold_version, scope=scope, dataset_name=bman.dataset_name,  # type: ignore[arg-type]
                            dataset_version=bman.dataset_version, bench_manifest_sha256=bsha,
                            labeling_protocol_version=labeling_protocol_version,
                            frozen_at=datetime.now(timezone.utc), frozen_by=frozen_by, entries=entries,
                            gold_hash=tree_hash((e.path, e.sha256) for e in entries))
    path = layout.gold_manifest_path(scope)
    if path.exists():
        old, _ = load_gold_manifest(layout, scope)
        if old.gold_version == gold_version:
            set_read_only(gdir)
            if old.gold_hash != manifest.gold_hash or old.bench_manifest_sha256 != bsha:
                raise IntegrityError(f"gold_version {gold_version!r} already frozen with different content; "
                                     "bump the version (versions are immutable)")
            return old
    content = model_to_pretty_json(manifest)
    _write_manifest(path, content)
    _archive(layout, f"{scope}_gold_manifest", gold_version, content)
    set_read_only(gdir)
    return manifest


def load_gold_manifest(layout: BenchLayout, scope: str) -> tuple[GoldManifest, str]:
    path = layout.gold_manifest_path(scope)
    if not path.exists():
        raise IntegrityError(f"{scope} gold manifest missing: {path} (gold has not been frozen)")
    try:
        m = GoldManifest.model_validate(read_json(path))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"{scope} gold manifest invalid or missing required metadata: {exc}") from exc
    return m, manifest_file_sha256(path)


def verify_gold(layout: BenchLayout, scope: str, *, expected_gold_manifest_sha256: str | None = None,
                expected_bench_manifest_sha256: str | None = None) -> tuple[GoldManifest, str]:
    _, bsha = verify_bench(layout, scope, expected_bench_manifest_sha256)
    manifest, gsha = load_gold_manifest(layout, scope)
    problems: list[str] = []
    if expected_gold_manifest_sha256 is not None and gsha != expected_gold_manifest_sha256:
        problems.append(f"gold manifest sha256 {gsha} != expected {expected_gold_manifest_sha256}")
    if manifest.bench_manifest_sha256 != bsha:
        problems.append("gold was frozen against a different bench manifest")
    found, probs = layout.discover_suffixed(layout.gold_dir(scope), ".gold.json")
    problems += probs
    listed = {e.path for e in manifest.entries}
    problems += [f"unlisted gold file {x}" for x in sorted({layout.rel(scope, p) for _, p in found} - listed)]
    for e in manifest.entries:
        p = layout.scope_root(scope) / e.path
        if not p.exists():
            problems.append(f"gold file missing: {e.path}")
            continue
        try:
            if file_canonical_sha256(p) != e.sha256:
                problems.append(f"gold hash mismatch: {e.path}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            problems.append(f"gold file unreadable {e.path}: {exc}")
    if tree_hash((e.path, e.sha256) for e in manifest.entries) != manifest.gold_hash:
        problems.append("gold_hash does not match the listed entries")
    if problems:
        raise IntegrityError(f"{scope} gold verification failed (fail closed):\n  " + "\n  ".join(problems))
    return manifest, gsha


def load_frozen_gold(layout: BenchLayout, scope: str, manifest: GoldManifest) -> dict[str, GoldLabel]:
    """Gold listed in a VERIFIED gold manifest (call verify_gold first)."""
    root = layout.scope_root(scope)
    return {e.item_id: GoldLabel.model_validate(read_json(root / e.path)) for e in manifest.entries}


__all__ = ["IntegrityError", "build_bench_manifest", "content_fingerprint", "freeze_gold", "is_read_only",
           "load_bench_manifest", "load_frozen_gold", "load_gold_manifest", "make_writable", "manifest_file_sha256",
           "set_read_only", "verify_bench", "verify_gold"]
