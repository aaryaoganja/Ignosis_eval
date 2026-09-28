"""Gold freeze, manifests and fail-closed hash verification."""

from __future__ import annotations

import json
import shutil

import pytest

from conftest import FIXTURE_ROOT, edit_json, edit_yaml
from ignosis_eval.integrity.freeze import (
    IntegrityError,
    build_benchmark_manifest,
    freeze_gold,
    is_read_only,
    load_frozen_gold,
    make_writable,
    verify_benchmark,
    verify_gold,
)

FREEZE = dict(gold_version="fixture-gold-1", frozen_by="fixture", labeling_protocol_version="fixture-protocol/0")


def test_committed_fixture_verifies():
    bm, bsha = verify_benchmark(FIXTURE_ROOT)
    gm, gsha = verify_gold(FIXTURE_ROOT, expected_benchmark_manifest_sha256=bsha)
    assert len(bm.cases) == 10 and len(gm.entries) == 10
    assert gm.benchmark_manifest_sha256 == bsha
    gold = load_frozen_gold(FIXTURE_ROOT, gm)
    assert set(gold) == {c.metadata.case_id for c in bm.cases}


def test_benchmark_manifest_records_required_metadata(smoke_root):
    bm, _ = verify_benchmark(smoke_root)
    c = bm.case("fx-0002")
    md = c.metadata
    assert (md.split.value, md.scenario, md.category, md.intended_modality.value, md.language) == \
        ("dev", "threat_after_hardship", "compliance_gate", "transcript_only", "en-IN")
    assert md.pair_id == "mp-0001" and md.judge_bait is False and md.synthetic is True
    assert bm.case("fx-0003").metadata.attribution_pair_id == "ap-0001"
    assert bm.case("fx-0009").metadata.judge_bait is True
    assert {f.path for f in c.files} == {"dataset/dev/fx-0002/input.json", "case_cards/dev/fx-0002.card.yaml"}


def test_gold_hash_change_fails_closed(smoke_root):
    edit_json(smoke_root / "gold" / "dev" / "fx-0001.gold.json", lambda d: d.update(expected_verdict="fail"))
    with pytest.raises(IntegrityError, match="gold hash mismatch"):
        verify_gold(smoke_root)


def test_gold_whitespace_only_change_is_not_drift(smoke_root):
    p = smoke_root / "gold" / "dev" / "fx-0001.gold.json"
    p.write_text(json.dumps(json.loads(p.read_text()), indent=7), encoding="utf-8")
    verify_gold(smoke_root)


def test_extra_or_missing_gold_fails_closed(smoke_root):
    g = smoke_root / "gold" / "dev"
    shutil.copy(g / "fx-0001.gold.json", g / "fx-0001b.gold.json")
    with pytest.raises(IntegrityError, match="unlisted gold file"):
        verify_gold(smoke_root)
    (g / "fx-0001b.gold.json").unlink()
    (g / "fx-0003.gold.json").unlink()
    with pytest.raises(IntegrityError, match="gold file missing"):
        verify_gold(smoke_root)


def test_benchmark_modification_without_manifest_change_fails_closed(smoke_root):
    edit_json(smoke_root / "dataset" / "dev" / "fx-0001" / "input.json",
              lambda d: d["transcript"]["turns"][0].update(text="Hi."))
    with pytest.raises(IntegrityError, match="hash mismatch"):
        verify_benchmark(smoke_root)


def test_case_card_edit_is_drift(smoke_root):
    edit_yaml(smoke_root / "case_cards" / "dev" / "fx-0001.card.yaml", lambda d: d.update(ambiguity_notes="changed"))
    with pytest.raises(IntegrityError, match="fx-0001.card.yaml"):
        verify_benchmark(smoke_root)


def test_added_or_removed_case_fails_closed(smoke_root):
    shutil.copytree(smoke_root / "dataset" / "dev" / "fx-0001", smoke_root / "dataset" / "dev" / "fx-0042")
    with pytest.raises(IntegrityError, match="not in manifest"):
        verify_benchmark(smoke_root)
    shutil.rmtree(smoke_root / "dataset" / "dev" / "fx-0042")
    shutil.rmtree(smoke_root / "dataset" / "calibration" / "fx-0010")
    with pytest.raises(IntegrityError, match="missing on disk"):
        verify_benchmark(smoke_root)


def test_unlisted_file_in_case_dir_fails_closed(smoke_root):
    (smoke_root / "dataset" / "dev" / "fx-0001" / "notes.txt").write_text("sneaky")
    with pytest.raises(IntegrityError, match="unlisted file"):
        verify_benchmark(smoke_root)


def test_missing_manifest_or_metadata_fails_closed(smoke_root):
    m = smoke_root / "manifests" / "benchmark_manifest.json"
    edit_json(m, lambda d: d.pop("dataset_version"))
    with pytest.raises(IntegrityError, match="missing required metadata"):
        verify_benchmark(smoke_root)
    m.unlink()
    with pytest.raises(IntegrityError, match="manifest missing"):
        verify_benchmark(smoke_root)
    (smoke_root / "manifests" / "gold_manifest.json").unlink()
    with pytest.raises(IntegrityError):
        verify_gold(smoke_root)


def test_expected_hash_mismatch_fails_closed(smoke_root):
    with pytest.raises(IntegrityError, match="expected"):
        verify_benchmark(smoke_root, expected_manifest_sha256="0" * 64)
    with pytest.raises(IntegrityError, match="expected"):
        verify_gold(smoke_root, expected_gold_manifest_sha256="0" * 64)


def test_gold_frozen_against_other_benchmark_fails(smoke_root):
    edit_json(smoke_root / "manifests" / "gold_manifest.json", lambda d: d.update(benchmark_manifest_sha256="1" * 64))
    with pytest.raises(IntegrityError, match="different benchmark manifest"):
        verify_gold(smoke_root)


def test_versions_are_immutable(smoke_root):
    # same version + same content: idempotent
    build_benchmark_manifest(smoke_root, dataset_name="smoke-fixture", dataset_version="fixture-1", created_by="t")
    freeze_gold(smoke_root, **FREEZE)
    # same gold version, different content: refused
    make_writable(smoke_root / "gold")
    edit_json(smoke_root / "gold" / "dev" / "fx-0001.gold.json", lambda d: d.update(confidence="moderate"))
    with pytest.raises(IntegrityError, match="immutable"):
        freeze_gold(smoke_root, **FREEZE)
    # new version: accepted, archived, read-only
    gm = freeze_gold(smoke_root, **{**FREEZE, "gold_version": "fixture-gold-2"})
    assert gm.gold_version == "fixture-gold-2"
    assert (smoke_root / "manifests" / "history" / "gold_manifest__fixture-gold-2.json").exists()
    verify_gold(smoke_root)
    # dataset: same version different content refused
    edit_json(smoke_root / "dataset" / "dev" / "fx-0001" / "input.json",
              lambda d: d["transcript"]["turns"][0].update(text="Hello, Acme Finance calling about your loan."))
    with pytest.raises(IntegrityError, match="immutable"):
        build_benchmark_manifest(smoke_root, dataset_name="smoke-fixture", dataset_version="fixture-1", created_by="t")


def test_freeze_marks_gold_read_only(smoke_root):
    make_writable(smoke_root / "gold")
    assert not is_read_only(smoke_root / "gold")
    freeze_gold(smoke_root, **FREEZE)
    assert is_read_only(smoke_root / "gold")


def test_freeze_refuses_unapproved_cards_and_missing_gold(smoke_root):
    edit_yaml(smoke_root / "case_cards" / "dev" / "fx-0001.card.yaml", lambda d: d.update(status="draft"))
    build_benchmark_manifest(smoke_root, dataset_name="smoke-fixture", dataset_version="fixture-2", created_by="t")
    with pytest.raises(IntegrityError, match="approved"):
        freeze_gold(smoke_root, **{**FREEZE, "gold_version": "g-x"})
    edit_yaml(smoke_root / "case_cards" / "dev" / "fx-0001.card.yaml", lambda d: d.update(status="approved"))
    build_benchmark_manifest(smoke_root, dataset_name="smoke-fixture", dataset_version="fixture-3", created_by="t")
    make_writable(smoke_root / "gold")
    (smoke_root / "gold" / "dev" / "fx-0005.gold.json").unlink()
    with pytest.raises(IntegrityError, match="B034"):
        freeze_gold(smoke_root, **{**FREEZE, "gold_version": "g-y"})


def test_freeze_refuses_protocol_mismatch_and_card_drift(smoke_root):
    with pytest.raises(IntegrityError, match="protocol"):
        freeze_gold(smoke_root, **{**FREEZE, "gold_version": "g-p", "labeling_protocol_version": "other/1"})
    make_writable(smoke_root / "gold")
    edit_json(smoke_root / "gold" / "dev" / "fx-0001.gold.json",
              lambda d: d["provenance"].update(case_card_sha256="2" * 64))
    with pytest.raises(IntegrityError, match="case card changed"):
        freeze_gold(smoke_root, **{**FREEZE, "gold_version": "g-q"})


def test_manifest_build_refuses_broken_benchmark(smoke_root):
    edit_yaml(smoke_root / "case_cards" / "holdout" / "fx-0008.card.yaml", lambda d: d.update(split="dev"))
    with pytest.raises(IntegrityError, match="errors"):
        build_benchmark_manifest(smoke_root, dataset_name="x", dataset_version="v9", created_by="t")


def test_repository_benchmark_manifest_is_empty_and_verifies():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "benchmark"
    bm, _ = verify_benchmark(root)
    assert bm.cases == [] and bm.dataset_version == "0.0.0-empty"
    with pytest.raises(IntegrityError, match="gold manifest missing"):
        verify_gold(root)
