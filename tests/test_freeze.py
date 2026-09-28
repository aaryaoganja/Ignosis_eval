"""Hash lists, gold freeze and fail-closed verification (P-1 rule 5, P-8 step 3; CLAUDE.md invariant 4)."""

from __future__ import annotations

import json

import pytest

import factories as F
from ignosis_eval.benchmark.layout import BenchLayout, LayoutError
from ignosis_eval.contracts.enums import Split
from ignosis_eval.integrity.freeze import (
    IntegrityError,
    build_bench_manifest,
    freeze_gold,
    is_read_only,
    make_writable,
    verify_bench,
    verify_gold,
)
from ignosis_eval.pipeline.normalize import unit_facts


def _bench(layout, spec):
    F.add_item(layout, "ZZ-F01")
    F.add_item(layout, "ZZ-F02", transcript=F.stub_transcript(F.STUB_TURNS[:3]))
    F.freeze(layout, spec)


def test_freeze_and_verify(layout, spec):
    _bench(layout, spec)
    m, sha = verify_bench(layout, "dev")
    assert [i.meta.item_id for i in m.items] == ["ZZ-F01", "ZZ-F02"]
    assert any(f.path.startswith("case_cards/") for f in m.items[0].files)  # cards are hash-listed
    assert m.items[0].units[0].unit_id == "ZZ-F01__TRANSCRIPT"
    gm, _ = verify_gold(layout, "dev", expected_bench_manifest_sha256=sha)
    assert len(gm.entries) == 2 and is_read_only(layout.gold_dir("dev"))


def test_drift_is_detected(layout, spec):
    _bench(layout, spec)
    t = layout.item_paths(Split.DEV, "ZZ-F01").item_dir / "transcript.txt"
    t.write_text(t.read_text() + "AGENT: stub extra\n")
    with pytest.raises(IntegrityError, match="hash mismatch"):
        verify_bench(layout, "dev")


def test_extra_and_missing_files(layout, spec):
    _bench(layout, spec)
    d = layout.item_paths(Split.DEV, "ZZ-F02").item_dir
    (d / "stray.txt").write_text("stub")
    with pytest.raises(IntegrityError, match="unlisted file"):
        verify_bench(layout, "dev")
    (d / "stray.txt").unlink()
    make_writable(layout.gold_dir("dev"))
    (layout.gold_dir("dev") / "ZZ-F02.gold.json").unlink()
    with pytest.raises(IntegrityError, match="gold file missing"):
        verify_gold(layout, "dev")


def test_gold_tamper_detected(layout, spec):
    _bench(layout, spec)
    p = layout.gold_dir("dev") / "ZZ-F01.gold.json"
    make_writable(p)
    data = json.loads(p.read_text())
    data["verdict"]["within_scope_complete"] = False
    p.write_text(json.dumps(data))
    with pytest.raises(IntegrityError, match="gold hash mismatch"):
        verify_gold(layout, "dev")


def test_version_reuse_refused(layout, spec):
    _bench(layout, spec)
    F.add_item(layout, "ZZ-F03", transcript=F.stub_transcript(F.STUB_TURNS[:2]))
    with pytest.raises(IntegrityError, match="immutable"):
        build_bench_manifest(layout, "dev", dataset_name="x", dataset_version="t1", created_by="t", facts_fn=unit_facts,
                             validator=lambda: [])


def test_gold_freeze_requires_approved_card(layout, spec):
    F.add_item(layout, "ZZ-F01", card_overrides={"status": "draft"})
    build_bench_manifest(layout, "dev", dataset_name="x", dataset_version="t1", created_by="t", facts_fn=unit_facts,
                         validator=lambda: [])
    with pytest.raises(IntegrityError, match="approved"):
        freeze_gold(layout, "dev", gold_version="g1", frozen_by="t", labeling_protocol_version="test-protocol",
                    validator=lambda: [])


def test_validator_errors_block_hash_listing(layout, spec):
    F.add_item(layout, "ZZ-F01")
    with pytest.raises(IntegrityError, match="refusing"):
        build_bench_manifest(layout, "dev", dataset_name="x", dataset_version="t1", created_by="t",
                             facts_fn=unit_facts, validator=lambda: ["ERROR something"])


def test_private_scope_rules(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_PRIVATE_DIR", raising=False)
    lay = BenchLayout(tmp_path / "bench")
    with pytest.raises(LayoutError, match="BENCH_PRIVATE_DIR"):
        lay.scope_root("private")
    with pytest.raises(LayoutError, match="outside"):
        BenchLayout(tmp_path / "bench", tmp_path / "bench" / "private")
    monkeypatch.setenv("BENCH_PRIVATE_DIR", str(tmp_path / "priv"))
    assert BenchLayout(tmp_path / "bench").scope_root("private") == (tmp_path / "priv").resolve()


def test_private_hash_list_lives_in_repo_bench(layout, spec):
    F.add_item(layout, "ZZ-H01", split="holdout")
    F.freeze(layout, spec, scope="private")
    m, _ = verify_bench(layout, "private")
    assert layout.manifest_path("private").parent == layout.root / "manifests"
    assert m.scope == "private" and m.items[0].meta.split is Split.HOLDOUT
    assert all(not f.path.startswith("/") for f in m.items[0].files)
