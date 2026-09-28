"""Bench checks (P-1 rules, §13, B-01 authoring constraints) on synthetic stub items."""

from __future__ import annotations

import factories as F
from ignosis_eval.benchmark.checks import check_bench


def _ids(layout, spec, **kw):
    rep = check_bench(layout, spec, **kw)
    return {(i.check_id, i.severity) for i in rep.issues}, rep


def test_clean_stub_bench_passes(layout, spec):
    F.add_item(layout, "ZZ-B01")
    F.add_item(layout, "ZZ-B02", transcript=F.stub_transcript(F.STUB_TURNS[:3]))
    ids, rep = _ids(layout, spec, require_gold=True)
    assert rep.ok, ids


def test_missing_card_orphan_gold_and_missing_gold(layout, spec):
    F.add_item(layout, "ZZ-B01")
    (layout.cards_dir("dev") / "ZZ-B01.card.yaml").unlink()
    (layout.gold_dir("dev") / "ZZ-B99.gold.json").write_text("{}")
    F.add_item(layout, "ZZ-B02", transcript=F.stub_transcript(F.STUB_TURNS[:2]))
    (layout.gold_dir("dev") / "ZZ-B02.gold.json").unlink()
    ids, _ = _ids(layout, spec, require_gold=True)
    assert {("B022", "error"), ("B030", "error"), ("B034", "error")} <= ids


def test_leakage_across_splits_and_pairs(layout, spec):
    F.add_item(layout, "ZZ-B01")
    F.add_item(layout, "ZZ-H01", split="holdout")  # same stub content in a private split
    F.write_registries(layout, {"pairs": [{"pair_id": "ZZ-P1", "clean_item": "ZZ-B01", "violating_item": "ZZ-H01",
                                           "target_check": "G3"}]})
    ids, _ = _ids(layout, spec, scopes=("dev", "private"))
    assert ("B008", "error") in ids and ("B011", "error") in ids


def test_pair_length_warning_and_registry_card_mismatch(layout, spec):
    F.write_registries(layout, {"pairs": [{"pair_id": "ZZ-P1", "clean_item": "ZZ-B01", "violating_item": "ZZ-B02",
                                           "target_check": "G3"}]})
    F.add_item(layout, "ZZ-B01", card_overrides={"pair": {"pair_id": "ZZ-P1", "role": "clean",
                                                          "counterpart_item_id": "ZZ-B02"}})
    F.add_item(layout, "ZZ-B02", transcript=F.stub_transcript(F.STUB_TURNS[:2]))  # no pair on the card
    ids, rep = _ids(layout, spec)
    assert ("B013", "warning") in ids
    assert any("CP001" in i.message for i in rep.issues)


def test_g7_item_needs_header_and_real_items_need_pii_review(layout, spec):
    F.add_item(layout, "ZZ-B01", card_overrides={"target_check": "G7", "severity": "CRITICAL",
                                                 "repair_status": "UNREPAIRED", "evidence_header": True})
    ids, rep = _ids(layout, spec)
    assert any("CX003" in i.message for i in rep.issues)
    F.add_item(layout, "ZZ-B02", transcript=F.stub_transcript(F.STUB_TURNS[:2]))
    meta = layout.item_paths(__import__("ignosis_eval.contracts.enums", fromlist=["Split"]).Split.DEV,
                             "ZZ-B02").meta_path
    meta.write_text(meta.read_text().replace('"synthetic": true', '"synthetic": false'))
    ids, _ = _ids(layout, spec)
    assert ("B015", "error") in ids


def test_gold_inconsistent_with_rubric_is_rejected(layout, spec):
    import json

    F.add_item(layout, "ZZ-B01")
    p = layout.gold_dir("dev") / "ZZ-B01.gold.json"
    data = json.loads(p.read_text())
    data["findings"] = [{"code": "EXE-01", "anchor_turns": [1], "severity": "MAJOR"}]
    data["verdict"]["value"] = "NEEDS_ATTENTION"
    p.write_text(json.dumps(data))
    ids, _ = _ids(layout, spec)
    assert ("B033", "error") in ids
