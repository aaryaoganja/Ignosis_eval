"""Experiment runner: run manifest, append-only storage, reproducibility, fail-closed behaviour, CLI."""

from __future__ import annotations

import csv
import json
import random
import stat
from pathlib import Path

import pytest

from conftest import edit_json
from factories import PROFILE_PATH
from ignosis_eval.cli import main
from ignosis_eval.contracts.enums import Split
from ignosis_eval.contracts.run_manifest import REP_FILES, RunManifest, rep_dir
from ignosis_eval.evaluators.k0 import KeywordFloorK0
from ignosis_eval.integrity.freeze import IntegrityError, make_writable
from ignosis_eval.runner import experiment as exp
from ignosis_eval.runner.experiment import RunConfig, RunConfigError, rep_seed, run_experiment
from ignosis_eval.runner.storage import AppendOnlyError, RunStore, write_json_once
from ignosis_eval.schemas import render_schemas
from ignosis_eval.scoring.loader import ScoringError
from ignosis_eval.scoring.scorer import score_run

REPO = Path(__file__).resolve().parents[1]


def _cfg(root, tmp_path, **kw) -> RunConfig:
    base = dict(benchmark_root=root, split=Split.DEV, evaluator="a", repetitions=2, seed=7,
                runs_root=tmp_path / "runs", scoring_root=tmp_path / "scoring", profile_path=PROFILE_PATH)
    base.update(kw)
    return RunConfig(**base)


def _records(run_dir: Path, manifest: RunManifest) -> dict[tuple[str, int], dict]:
    out = {}
    for item in manifest.dataset.item_ids:
        for r in range(1, manifest.repetitions + 1):
            p = rep_dir(run_dir, item, r) / "evaluation_record.json"
            out[(item, r)] = json.loads(p.read_text()) if p.exists() else None
    return out


def _manifest(run_dir: Path) -> RunManifest:
    return RunManifest.model_validate(json.loads((run_dir / "manifest.json").read_text()))


# ------------------------------------------------------------------------------------------ happy path
def test_end_to_end_run_layout_manifest_and_scoring(smoke_root, tmp_path):
    res = run_experiment(_cfg(smoke_root, tmp_path, evaluator_options={"noise_rate": 0.2}))
    assert res.completion.status == "completed"
    assert res.completion.n_records_written == res.completion.n_item_reps_expected == 14
    m = _manifest(res.run_dir)
    # required run-manifest metadata
    assert m.dataset.split is Split.DEV and m.dataset.n_items == 7
    assert m.dataset.dataset_hash and m.gold and m.gold.gold_hash and m.gold.gold_version == "fixture-gold-1"
    assert m.profile.sha256 and m.profile.status == "placeholder" and m.rubric_version == m.profile.rubric_version
    assert m.evaluator.prompt_hashes and m.evaluator.model_id == "mock-llm/0" and m.evaluator.temperature == 0.0
    assert m.evaluator.retry_policy.max_attempts == 3 and m.evaluator.options == {"noise_rate": 0.2}
    assert len(m.git.commit) == 40
    assert m.asr is not None and m.asr.engine == "mock-asr"  # audio-only items present in dev
    assert m.audio_rendering.n_items_with_audio == 4 and m.audio_rendering.renderings
    assert m.repetitions == 2 and m.randomization.seed == 7 and m.created_at
    # storage layout
    for item in m.dataset.item_ids:
        for r in (1, 2):
            d = rep_dir(res.run_dir, item, r)
            assert {p.name for p in d.iterdir()} == set(REP_FILES)
            rec = json.loads((d / "evaluation_record.json").read_text())
            assert rec["experiment"]["item_id"] == item and rec["experiment"]["repetition"] == r
            assert rec["experiment"]["rep_seed"] == rep_seed(7, item, r)
    assert (res.run_dir / "completion.json").exists()
    # scoring outputs
    names = {p.name for p in res.scoring_dir.iterdir()}
    assert names == {"item_scores.csv", "metrics.json", "discordance_tables.csv", "scoring_manifest.json"}
    rows = list(csv.DictReader((res.scoring_dir / "item_scores.csv").open()))
    assert len(rows) == 14
    metrics = res.metrics
    assert metrics["gold_version"] == "fixture-gold-1" and metrics["provisional_definitions"] is True
    warnings = " ".join(metrics["warnings"])
    for needle in ("PLACEHOLDER", "mock", "Non-official", "test_fixture", "provisional"):
        assert needle in warnings, needle
    assert metrics["metrics"]["integrity_failures"]["k"] == 0
    assert set(metrics["slices"]["by_input_mode"]) == {"audio_only", "audio_transcript", "transcript_only"}
    assert metrics["language_delta"]["reference"] == "en-IN"
    ledger = [json.loads(line) for line in (tmp_path / "runs" / "LEDGER.jsonl").read_text().splitlines()]
    assert [e["event"] for e in ledger] == ["start", "finish"]


@pytest.mark.parametrize("evaluator,split", [("k0", Split.REDTEAM), ("b", Split.CALIBRATION),
                                             ("a_plus", Split.DEV)])
def test_every_evaluator_runs_through_the_framework(smoke_root, tmp_path, evaluator, split):
    res = run_experiment(_cfg(smoke_root, tmp_path, evaluator=evaluator, split=split, repetitions=1))
    assert res.completion.status == "completed"
    assert res.metrics["metrics"]["integrity_failures"]["k"] == 0


# ------------------------------------------------------------------------------------------ append-only
def test_append_only_results(smoke_root, tmp_path):
    res = run_experiment(_cfg(smoke_root, tmp_path, repetitions=1))
    rec = rep_dir(res.run_dir, "fx-0001", 1) / "evaluation_record.json"
    before = rec.read_text()
    with pytest.raises(AppendOnlyError):
        write_json_once(rec, {"verdict": "fail"})
    assert rec.read_text() == before
    assert not rec.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    store = RunStore(tmp_path / "runs")
    with pytest.raises(AppendOnlyError):
        store.create_run(res.run_id)
    with pytest.raises(ScoringError, match="append-only"):
        score_run(res.run_dir, smoke_root, scoring_root=tmp_path / "scoring")
    out, _ = score_run(res.run_dir, smoke_root, scoring_root=tmp_path / "scoring", out_suffix="rescore")
    assert out.name == f"{res.run_id}--rescore"
    # a second run never touches the first
    res2 = run_experiment(_cfg(smoke_root, tmp_path, repetitions=1))
    assert res2.run_id != res.run_id and rec.read_text() == before


# ------------------------------------------------------------------------------------------ reproducibility
def test_run_reproducibility(smoke_root, tmp_path):
    cfg = dict(evaluator_options={"noise_rate": 0.4}, repetitions=3, seed=11, score=False)
    r1 = run_experiment(_cfg(smoke_root, tmp_path, **cfg))
    r2 = run_experiment(_cfg(smoke_root, tmp_path, **cfg))
    m1, m2 = _manifest(r1.run_dir), _manifest(r2.run_dir)
    assert m1.dataset == m2.dataset and m1.evaluator == m2.evaluator and m1.gold == m2.gold
    rec1, rec2 = _records(r1.run_dir, m1), _records(r2.run_dir, m2)

    def strip(r):
        return {k: v for k, v in r.items() if k != "experiment"}

    assert {k: strip(v) for k, v in rec1.items()} == {k: strip(v) for k, v in rec2.items()}
    for item in m1.dataset.item_ids:
        for r in (1, 2, 3):
            for f in ("normalized_input.json", "llm_requests.jsonl"):
                assert (rep_dir(r1.run_dir, item, r) / f).read_text() == (rep_dir(r2.run_dir, item, r) / f).read_text()
    # repetitions differ in seed, so noisy evaluators can disagree across reps (consistency is measurable)
    assert len({rep_seed(11, "fx-0002", r) for r in (1, 2, 3)}) == 3


def test_shuffled_order_is_seeded_and_recorded(smoke_root, tmp_path):
    res = run_experiment(_cfg(smoke_root, tmp_path, shuffle=True, seed=5, repetitions=1, score=False))
    m = _manifest(res.run_dir)
    expected = sorted(m.dataset.item_ids)
    random.Random(5).shuffle(expected)
    assert m.dataset.item_ids == expected and m.randomization.item_order == "shuffled"


# ------------------------------------------------------------------------------------------ fail closed
class _Crashy(KeywordFloorK0):
    def evaluate(self, inp, ctx):
        if inp.call_id == "call-7f3a02":
            raise RuntimeError("model exploded")
        return super().evaluate(inp, ctx)


class _GoldPeeker(KeywordFloorK0):
    def __init__(self, gold_file: Path):
        self.gold_file = gold_file

    def evaluate(self, inp, ctx):
        json.loads(self.gold_file.read_text())  # attempt to read the answer key
        return super().evaluate(inp, ctx)


def test_evaluator_crash_is_recorded_and_scored_as_integrity_failure(smoke_root, tmp_path):
    res = run_experiment(_cfg(smoke_root, tmp_path, repetitions=1), evaluator=_Crashy())
    assert res.completion.status == "completed" and res.completion.n_item_reps_with_errors == 1
    d = rep_dir(res.run_dir, "fx-0002", 1)
    assert not (d / "evaluation_record.json").exists()
    errors = json.loads((d / "errors.json").read_text())
    assert errors[0]["stage"] == "evaluate" and errors[0]["type"] == "RuntimeError"
    m = res.metrics["metrics"]
    assert m["integrity_failures"]["breakdown"]["by_code"] == {"missing_record": 1}
    assert m["critical_misses"]["breakdown"]["misses_by_missing_record"] == 1


def test_evaluator_touching_gold_fails_the_run_closed(smoke_root, tmp_path):
    gold_file = smoke_root / "gold" / "dev" / "fx-0001.gold.json"
    with pytest.raises(IntegrityError, match="forbidden operation"):
        run_experiment(_cfg(smoke_root, tmp_path, repetitions=1), evaluator=_GoldPeeker(gold_file))
    run_dir = next((tmp_path / "runs").glob("2*"))
    completion = json.loads((run_dir / "completion.json").read_text())
    assert completion["status"] == "failed"
    errors = json.loads((next(run_dir.glob("*/rep_1")) / "errors.json").read_text())
    assert errors[0]["type"] == "ProtectedPathViolation"
    with pytest.raises(ScoringError, match="failed"):
        score_run(run_dir, smoke_root, scoring_root=tmp_path / "scoring")


def test_tampered_gold_or_benchmark_blocks_the_run(smoke_root, tmp_path):
    make_writable(smoke_root / "gold")
    edit_json(smoke_root / "gold" / "dev" / "fx-0001.gold.json", lambda d: d.update(expected_verdict="fail"))
    with pytest.raises(IntegrityError, match="gold"):
        run_experiment(_cfg(smoke_root, tmp_path))
    assert not (tmp_path / "runs").exists()  # nothing was started


def test_post_run_verification_detects_drift(smoke_root, tmp_path):
    from ignosis_eval.integrity.freeze import verify_gold

    gm, gsha = verify_gold(smoke_root)
    from ignosis_eval.integrity.freeze import verify_benchmark
    _, bsha = verify_benchmark(smoke_root)
    assert exp._post_run_verification(smoke_root, bsha, gsha) is None
    make_writable(smoke_root / "gold")
    edit_json(smoke_root / "gold" / "dev" / "fx-0003.gold.json", lambda d: d.update(confidence="low"))
    assert "gold changed" in exp._post_run_verification(smoke_root, bsha, gsha)


def test_scoring_refuses_gold_or_profile_changed_after_run(smoke_root, tmp_path):
    res = run_experiment(_cfg(smoke_root, tmp_path, repetitions=1, score=False))
    other_profile = tmp_path / "profile.yaml"
    other_profile.write_text(PROFILE_PATH.read_text().replace("Placeholder collections profile",
                                                               "Edited placeholder profile"))
    with pytest.raises(ScoringError, match="profile hash"):
        score_run(res.run_dir, smoke_root, scoring_root=tmp_path / "scoring", profile_path=other_profile)
    make_writable(smoke_root / "gold")
    edit_json(smoke_root / "gold" / "dev" / "fx-0004.gold.json", lambda d: d.update(expected_verdict="fail"))
    with pytest.raises(IntegrityError):
        score_run(res.run_dir, smoke_root, scoring_root=tmp_path / "scoring")


def test_scoring_refuses_incomplete_or_invalid_run(smoke_root, tmp_path):
    res = run_experiment(_cfg(smoke_root, tmp_path, repetitions=1, score=False))
    make_writable(res.run_dir)
    (res.run_dir / "completion.json").unlink()
    with pytest.raises(ScoringError, match="no completion record"):
        score_run(res.run_dir, smoke_root, scoring_root=tmp_path / "scoring")
    out, _ = score_run(res.run_dir, smoke_root, scoring_root=tmp_path / "scoring", allow_incomplete=True)
    assert json.loads((out / "scoring_manifest.json").read_text())["allow_incomplete"] is True
    edit_json(res.run_dir / "manifest.json", lambda d: d.pop("git"))
    with pytest.raises(ScoringError, match="missing required metadata"):
        score_run(res.run_dir, smoke_root, scoring_root=tmp_path / "scoring", allow_incomplete=True, out_suffix="x")


def test_holdout_and_official_preconditions(smoke_root, tmp_path, monkeypatch):
    with pytest.raises(RunConfigError, match="confirm-holdout"):
        run_experiment(_cfg(smoke_root, tmp_path, split=Split.HOLDOUT))
    res = run_experiment(_cfg(smoke_root, tmp_path, split=Split.HOLDOUT, confirm_holdout=True, repetitions=1))
    assert res.completion.status == "completed"
    with pytest.raises(RunConfigError, match="whole split"):
        run_experiment(_cfg(smoke_root, tmp_path, official=True, item_ids=["fx-0001"]))
    from ignosis_eval.contracts.run_manifest import GitInfo
    monkeypatch.setattr(exp, "git_info", lambda: GitInfo(commit="a" * 40, branch="x", dirty=True))
    with pytest.raises(RunConfigError, match="clean git"):
        run_experiment(_cfg(smoke_root, tmp_path, official=True))
    monkeypatch.setattr(exp, "git_info", lambda: GitInfo(commit="a" * 40, branch="x", dirty=False))
    with pytest.raises(RunConfigError, match="placeholder"):
        run_experiment(_cfg(smoke_root, tmp_path, official=True))


def test_run_manifest_contract_requires_metadata():
    from pydantic import ValidationError
    good = json.loads(json.dumps(_example_manifest()))
    RunManifest.model_validate(good)
    for key in ("git", "dataset", "evaluator", "randomization", "repetitions", "profile"):
        bad = dict(good)
        bad.pop(key)
        with pytest.raises(ValidationError):
            RunManifest.model_validate(bad)
    bad = json.loads(json.dumps(good))
    bad["evaluator"]["model_id"] = None
    with pytest.raises(ValidationError, match="model_id"):
        RunManifest.model_validate(bad)
    bad = json.loads(json.dumps(good))
    bad.update(official=True)
    bad["git"]["dirty"] = True
    with pytest.raises(ValidationError, match="official"):
        RunManifest.model_validate(bad)


def _example_manifest() -> dict:
    h = "0" * 64
    return {
        "run_id": "r1", "created_at": "2026-09-28T00:00:00Z", "official": False,
        "dataset": {"name": "d", "version": "1", "split": "dev", "benchmark_manifest_sha256": h, "dataset_hash": h,
                    "split_hash": h, "item_ids": ["c-001"]},
        "gold": {"gold_version": "g1", "gold_manifest_sha256": h, "gold_hash": h, "split_hash": h},
        "rubric_version": "r", "profile": {"profile_id": "p", "profile_version": "1", "rubric_version": "r",
                                           "sha256": h, "path": "p.yaml", "status": "draft"},
        "evaluator": {"name": "e", "version": "1", "architecture": "A", "llm_backend": "mock", "model_id": "m",
                      "temperature": 0.0, "retry_policy": {"max_attempts": 1, "backoff_initial_s": 0,
                                                           "backoff_multiplier": 1}, "config_hash": h},
        "component_versions": {}, "git": {"commit": "a" * 40, "dirty": False}, "asr": None,
        "audio_rendering": {"n_items_with_audio": 0}, "repetitions": 1,
        "randomization": {"seed": 1, "item_order": "manifest", "rep_seed_derivation": "x"},
        "environment": {}, "invocation": [],
    }


# ------------------------------------------------------------------------------------------ CLI / schemas
def test_cli_run_and_score(smoke_root, tmp_path, capsys):
    args = ["run", "--benchmark-root", str(smoke_root), "--split", "redteam", "--evaluator", "k0", "--reps", "1",
            "--seed", "3", "--runs-root", str(tmp_path / "runs"), "--scoring-root", str(tmp_path / "scoring"),
            "--profile", str(PROFILE_PATH)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "completed" in out and "WARNING: Evaluation profile is a PLACEHOLDER" in out
    run_dir = next((tmp_path / "runs").glob("2*"))
    score = ["score", "--run-dir", str(run_dir), "--benchmark-root", str(smoke_root),
             "--scoring-root", str(tmp_path / "scoring")]
    assert main(score) == 3  # already scored: append-only
    assert main([*score, "--suffix", "again", "--min-n", "1"]) == 0
    assert main(["run", "--benchmark-root", str(smoke_root), "--split", "holdout", "--evaluator", "k0",
                 "--seed", "1", "--runs-root", str(tmp_path / "runs"), "--profile", str(PROFILE_PATH)]) == 3


def test_exported_json_schemas_are_up_to_date():
    for name, text in render_schemas().items():
        p = REPO / "schemas" / f"{name}.schema.json"
        assert p.exists() and p.read_text() == text, f"run `ignosis-eval schemas export` ({name})"
