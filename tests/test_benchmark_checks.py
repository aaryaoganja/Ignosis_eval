"""Benchmark integrity checks: pair splits, duplicate ids, gold references, holdout discipline, leakage."""

from __future__ import annotations

import json
import shutil

import pytest

from conftest import edit_json, edit_yaml
from factories import PROFILE
from ignosis_eval.benchmark.checks import BenchmarkIntegrityError, check_benchmark
from ignosis_eval.benchmark.holdout import register_holdout


def _errors(root, **kw) -> set[str]:
    rep = check_benchmark(root, profile=PROFILE, **kw)
    return {i.check_id for i in rep.errors} | {i.message.split(":")[0] for i in rep.errors if i.check_id == "B020"}


def _move_case(root, case_id, src, dst, update_fields=True):
    shutil.move(root / "dataset" / src / case_id, root / "dataset" / dst / case_id)
    (root / "case_cards" / dst).mkdir(parents=True, exist_ok=True)
    (root / "gold" / dst).mkdir(parents=True, exist_ok=True)
    shutil.move(root / "case_cards" / src / f"{case_id}.card.yaml", root / "case_cards" / dst / f"{case_id}.card.yaml")
    shutil.move(root / "gold" / src / f"{case_id}.gold.json", root / "gold" / dst / f"{case_id}.gold.json")
    if update_fields:
        edit_yaml(root / "case_cards" / dst / f"{case_id}.card.yaml", lambda d: d.update(split=dst))
        edit_json(root / "gold" / dst / f"{case_id}.gold.json", lambda d: d.update(split=dst))


def test_fixture_passes_all_checks(smoke_root):
    rep = check_benchmark(smoke_root, require_gold=True, profile=PROFILE)
    assert rep.ok, [str(i) for i in rep.errors]
    assert len(rep.cases) == 10


def test_pair_crossing_splits_detected(smoke_root):
    _move_case(smoke_root, "fx-0002", "dev", "redteam")
    assert "CS04" in _errors(smoke_root)


def test_attribution_pair_crossing_splits_detected(smoke_root):
    _move_case(smoke_root, "fx-0004", "dev", "calibration")
    assert "CS06" in _errors(smoke_root)


def test_duplicate_case_id_across_splits(smoke_root):
    shutil.copytree(smoke_root / "dataset" / "dev" / "fx-0001", smoke_root / "dataset" / "redteam" / "fx-0001")
    errs = _errors(smoke_root)
    assert "B003" in errs


def test_duplicate_call_id_and_content_leakage(smoke_root):
    src = smoke_root / "dataset" / "holdout" / "fx-0008"
    dst = smoke_root / "dataset" / "dev" / "fx-0099"
    shutil.copytree(src, dst)
    errs = _errors(smoke_root)
    assert "B006" in errs  # same call_id
    edit_json(dst / "input.json", lambda d: d.update(call_id="call-other"))
    errs = _errors(smoke_root)
    assert "B008" in errs  # identical content in holdout and dev
    assert "B022" in errs  # and it has no case card


def test_missing_gold_reference(smoke_root):
    (smoke_root / "gold" / "dev" / "fx-0003.gold.json").unlink()
    assert "B034" not in _errors(smoke_root)  # warning while authoring
    assert "B034" in _errors(smoke_root, require_gold=True)  # error when gold is required


def test_orphan_gold_detected(smoke_root):
    shutil.copy(smoke_root / "gold" / "dev" / "fx-0001.gold.json", smoke_root / "gold" / "dev" / "fx-0777.gold.json")
    assert "B030" in _errors(smoke_root)


def test_holdout_accidentally_marked_dev(smoke_root):
    # card says dev while the case sits in holdout
    edit_yaml(smoke_root / "case_cards" / "holdout" / "fx-0008.card.yaml", lambda d: d.update(split="dev"))
    assert "B021" in _errors(smoke_root)


def test_holdout_gold_marked_dev(smoke_root):
    edit_json(smoke_root / "gold" / "holdout" / "fx-0008.gold.json", lambda d: d.update(split="dev"))
    assert "B031" in _errors(smoke_root)


def test_registered_holdout_case_moved_to_dev(smoke_root):
    _move_case(smoke_root, "fx-0008", "holdout", "dev")  # consistent move: only the registry can catch it
    errs = _errors(smoke_root)
    assert "B040" in errs


def test_holdout_content_change_after_registration(smoke_root):
    edit_json(smoke_root / "dataset" / "holdout" / "fx-0008" / "input.json",
              lambda d: d["transcript"]["turns"][2].update(text="I will pay on Sunday."))
    assert "B040" in _errors(smoke_root)


def test_register_holdout_is_append_only_and_refuses_broken_benchmark(smoke_root):
    reg = smoke_root / "manifests" / "holdout_registry.json"
    before = json.loads(reg.read_text())
    assert register_holdout(smoke_root) == []  # nothing new
    assert json.loads(reg.read_text()) == before
    edit_yaml(smoke_root / "case_cards" / "holdout" / "fx-0008.card.yaml", lambda d: d.update(split="dev"))
    with pytest.raises(BenchmarkIntegrityError):
        register_holdout(smoke_root)


def test_card_modality_and_language_must_match_input(smoke_root):
    edit_yaml(smoke_root / "case_cards" / "dev" / "fx-0001.card.yaml",
              lambda d: d["modality"].update(intended_modality="audio_only"))
    edit_yaml(smoke_root / "case_cards" / "dev" / "fx-0005.card.yaml", lambda d: d.update(language="hi-IN"))
    errs = _errors(smoke_root)
    assert {"CX02", "CX03"} <= errs


def test_audio_hash_mismatch_and_invalid_input(smoke_root):
    (smoke_root / "dataset" / "dev" / "fx-0003" / "audio.wav").write_bytes(b"RIFF-tampered")
    edit_json(smoke_root / "dataset" / "dev" / "fx-0001" / "input.json", lambda d: d.update(input_mode="video"))
    errs = _errors(smoke_root)
    assert {"B007", "B005"} <= errs


def test_gold_ids_checked_against_profile(smoke_root):
    edit_json(smoke_root / "gold" / "dev" / "fx-0002.gold.json",
              lambda d: d["expected_defects"][0].update(defect_id="DEF_NOT_IN_PROFILE"))
    assert "B036" in _errors(smoke_root)


def test_stray_split_directory(smoke_root):
    (smoke_root / "dataset" / "tuning").mkdir()
    assert "B001" in _errors(smoke_root)


def test_empty_repository_benchmark_is_clean():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "benchmark"
    rep = check_benchmark(root, profile=PROFILE)
    assert rep.ok and rep.cases == {}
