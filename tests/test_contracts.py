"""Contract rules (frozen-contract §5, §9, §17; scoring-spec SD-01/SD-02) and schema sync."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import factories as F
from ignosis_eval.contracts.benchmark import ItemMeta
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, GateResult
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.contracts.registries import Registries
from ignosis_eval.contracts.run_manifest import RunManifest
from ignosis_eval.schemas import render_schemas


def test_evaluation_record_rules():
    rec = F.record()
    data = rec.model_dump(mode="json")
    data["gates"] = data["gates"][:-1]
    with pytest.raises(ValidationError):  # SD-02: every gate present in an OK record
        EvaluationRecord.model_validate(data)
    with pytest.raises(ValidationError):  # critical_status iff FAIL
        GateResult(gate="G3", status="FAIL")
    cf = F.record(gates={"G3": "FAIL"}).model_dump(mode="json")
    with pytest.raises(ValidationError):  # critical_status iff CRITICAL_FAIL
        EvaluationRecord.model_validate({**cf, "critical_status": None})
    f = F.failed()
    assert f.verdict is None
    bad = f.model_dump(mode="json")
    bad["verdict"] = "MEETS_BAR"
    with pytest.raises(ValidationError):  # V0: EVALUATION_FAILED never carries a verdict
        EvaluationRecord.model_validate(bad)
    with pytest.raises(ValidationError):  # unknown enum values are schema errors (SD-02)
        EvaluationRecord.model_validate({**rec.model_dump(mode="json"), "verdict": "PASSABLE"})
    assert rec.content_hash() == rec.model_copy(update={"experiment": None}).content_hash()


def test_gold_label_invariants():
    g = F.gold()
    assert g.provenance.derived_from_evaluator_output is False
    raw = g.model_dump(mode="json")
    with pytest.raises(ValidationError):  # gold can never be derived from evaluator output
        GoldLabel.model_validate({**raw, "provenance": {**raw["provenance"], "derived_from_evaluator_output": True}})
    with pytest.raises(ValidationError):  # every gate explicit
        GoldLabel.model_validate({**raw, "gates": {k: v for k, v in raw["gates"].items() if k != "G5"}})
    with pytest.raises(ValidationError):  # INCONCLUSIVE needs trigger Y/N
        F.gold(gates={"G1": {"status": "INCONCLUSIVE"}})
    with pytest.raises(ValidationError):  # gate FAIL => CRITICAL_FAIL
        F.gold(gates={"G1": {"status": "FAIL"}}, verdict="MEETS_BAR")
    with pytest.raises(ValidationError):  # holdout labelers blind to evaluator outputs
        GoldLabel.model_validate({**raw, "split": "holdout", "labelers": [
            {**raw["labelers"][0], "saw_evaluator_outputs": True}]})
    with pytest.raises(ValidationError):  # a code cannot be a finding and in an explicit list
        F.gold(findings=[{"code": "UND-01", "anchor_turns": [1], "severity": "MAJOR"}], na_checks=["UND-01"],
               verdict="NEEDS_ATTENTION")


def test_item_meta_rules():
    ok = {"item_id": "ZZ-P01", "split": "holdout", "pack": "core", "language": "hi-en",
          "unit_modes": ["TRANSCRIPT", "T-gold", "T-asr", "A", "A+T"],
          "artifacts": {"transcript": "t.txt", "audio": "a.wav"}}
    ItemMeta.model_validate(ok)  # a textual item with audio renderings (P-01@audio) shares one item / one gold
    with pytest.raises(ValidationError):
        ItemMeta.model_validate({**ok, "unit_modes": ["A+T-platform"]})  # needs a platform transcript
    with pytest.raises(ValidationError):
        ItemMeta.model_validate({**ok, "item_id": "zz-lower"})  # canonical bench-a1 ids (R-07)
    with pytest.raises(ValidationError):
        ItemMeta.model_validate({**ok, "pack": "redteam"})  # red-team items belong to the redteam split
    assert ItemMeta.model_validate({**ok, "pack": "calibration"}).scoring_role == "never"


def test_registries():
    with pytest.raises(ValidationError):
        Registries.model_validate({"pairs": [{"pair_id": "P", "clean_item": "ZZ-A1", "violating_item": "ZZ-A1",
                                              "target_check": "G3"}]})
    with pytest.raises(ValidationError):
        Registries.model_validate({"controls": [{"item_id": "ZZ-A1", "target_gates": ["G3"]},
                                                {"item_id": "ZZ-A2", "target_gates": []}]})
    pair = {"pair_id": "P", "clean_item": "ZZ-A1", "violating_item": "ZZ-A2", "target_check": "COM-02"}
    ok = Registries.model_validate({"pairs": [{**pair, "incidental_differences": [
        {"aspect": "borrower_context", "where": "B4", "basis": "BD-03"},
        {"aspect": "call_start_ts_header", "where": "G7", "basis": "BD-04"}]}]})  # BD-03 / BD-04 pair metadata
    assert [d.basis for d in ok.pairs[0].incidental_differences] == ["BD-03", "BD-04"]
    for bad in ({"aspect": "borrower_context", "where": "COM-02", "basis": "BD-03"},  # never the pair target
                {"aspect": "borrower_context", "where": "B4", "basis": "SC-04"},     # basis must be a BD id
                {"aspect": "tone", "where": "B4", "basis": "BD-03"}):
        with pytest.raises(ValidationError):
            Registries.model_validate({"pairs": [{**pair, "incidental_differences": [bad]}]})


def _manifest(**over):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    sha = "a" * 64
    m = {"run_id": "r", "kind": "dev", "locked": False, "created_at": now,
         "dataset": {"name": "b", "version": "1", "split": "dev", "scope": "dev", "bench_manifest_sha256": sha,
                     "dataset_hash": sha, "registries_sha256": sha, "units": ["ZZ-A1__TRANSCRIPT"]},
         "gold": {"gold_version": "1", "gold_manifest_sha256": sha, "gold_hash": sha},
         "spec": {"contract_version": "1.0.0-frozen", "rubric_version": "1.0-mvp", "rubric_sha256": sha,
                  "profile_id": "collections_default_v1", "profile_version": "1", "profile_sha256": sha,
                  "profile_path": "p", "profile_is_canonical": True, "lexicons_sha256": sha, "spec_file_sha256": {}},
         "systems": [{"system": "K0", "version": "1", "llm_backend": "none"}],
         "asr": {"status": "not_used"}, "diarization": {"status": "not_used"},
         "telephony_simulation": {"status": "not_used"}, "price_snapshot": {"status": "pending_signoff"},
         "base_seed": 1, "repetitions": 5, "ordering": "P-6", "alias_mapping_sha256": sha,
         "unit_alias_mapping_sha256": sha, "p17_payload_strings_checked": 1,
         "git": {"commit": "a" * 40, "dirty": False},
         "hash_verification": {"verified_at": now, "bench_manifest": True, "item_files": True,
                               "private_hash_list": None, "gold": True, "rubric": True, "profile": True,
                               "lexicons": True}}
    for k, v in over.items():
        m[k] = v if not isinstance(v, dict) or k not in m or not isinstance(m[k], dict) else {**m[k], **v}
    return m


def test_run_manifest_lock_rules():
    RunManifest.model_validate(_manifest())
    for missing in ("unit_alias_mapping_sha256", "p17_payload_strings_checked"):  # P-17 is recorded (fail closed)
        with pytest.raises(ValidationError):
            RunManifest.model_validate({k: v for k, v in _manifest().items() if k != missing})
    with pytest.raises(ValidationError):  # holdout data only in a locked run
        RunManifest.model_validate(_manifest(dataset={"split": "holdout", "scope": "private"}))
    locked = dict(kind="locked_holdout", locked=True, dataset={"split": "holdout", "scope": "private"},
                  hash_verification={"private_hash_list": True})
    with pytest.raises(ValidationError):  # no tag
        RunManifest.model_validate(_manifest(**locked))
    RunManifest.model_validate(_manifest(**locked, git={"tag": "eval-freeze-v1"}))
    for extra in ({"pending_signoff": [{"path": "x", "blocks_locked_run": True}]},
                  {"spec": {"profile_is_canonical": False}},
                  {"systems": [{"system": "A", "version": "1", "llm_backend": "mock_replay", "model_snapshot_id": "m",
                                "temperature": 0.0, "schema_retries": 1, "transport_retry": {}}]}):
        with pytest.raises(ValidationError):
            RunManifest.model_validate(_manifest(**locked, git={"tag": "eval-freeze-v1"}, **extra))
    with pytest.raises(ValidationError):  # A+ requires A
        RunManifest.model_validate(_manifest(systems=[{"system": "A+", "version": "1", "llm_backend": "none",
                                                       "derived_from": "A"}]))


def test_schemas_in_sync():
    rendered = render_schemas()
    for name, text in rendered.items():
        path = F.REPO / "schemas" / f"{name}.schema.json"
        assert path.exists() and path.read_text(encoding="utf-8") == text, \
            f"schemas/{name}.schema.json out of date: run `ignosis-eval schemas export`"
    assert json.loads(rendered["evaluation_record"])["title"] == "EvaluationRecord"
