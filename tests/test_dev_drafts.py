"""DEV draft runs (runner/dev_drafts.py) and the intent-referenced baseline (devbaseline/).

The drafts are read, never changed. LLM traffic comes from replay fixtures with obviously synthetic stub content, so
these runs measure plumbing only; nothing here is an evaluation result.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import factories as F
from ignosis_eval.benchmark.public_dev import validate_public_dev
from ignosis_eval.contracts.enums import System
from ignosis_eval.devbaseline.metrics import UNMEASURED, ItemRef, Unit, classify, pair_results, system_metrics
from ignosis_eval.devbaseline.report import build_report, render_markdown
from ignosis_eval.evaluators.base import input_sha256
from ignosis_eval.pipeline.normalize import build_normalized_input
from ignosis_eval.runner.dev_drafts import (
    DRAFT_UNIT,
    DraftRunConfig,
    DraftRunError,
    draft_meta,
    evaluator_configuration,
    frontend_stability,
    run_dev_drafts,
)

SP = F.spec()
BENCH = F.REPO / "bench"
DRAFTS = BENCH / "dev" / "transcripts"
DESIGN = validate_public_dev(BENCH / "public", SP).design


def _cfg(tmp_path: Path, **kw) -> DraftRunConfig:
    return DraftRunConfig(drafts_dir=DRAFTS, bench_root=BENCH, results_root=tmp_path, spec_dir=F.SPEC_DIR, **kw)


def test_k0_draft_run_scored_items_only(tmp_path):
    res = run_dev_drafts(_cfg(tmp_path))
    m = json.loads((res.run_dir / "draft_run.json").read_text(encoding="utf-8"))
    assert res.completion["status"] == "completed" and res.completion["n_records_written"] == 18
    assert m["official"] is False and m["drafts"]["human_review_pending"] is True
    assert set(m["items"]["excluded"]) == {"G-02-N5", *(f"SN-D0{n}" for n in range(1, 7))}
    assert m["drafts"]["assisting_families"] == ["anthropic-claude"] and m["p17_payload_strings_checked"] > 0
    assert {i: v["prechecks"]["G7"] for i, v in m["frontend"].items() if v["prechecks"]["G7"] != "OUT_OF_SCOPE"} == {
        "G-02": "PASS", "K-07": "PASS"}  # no G7 positive (BD-02)
    mapping = json.loads((res.run_dir / "unit_alias_mapping.json").read_text(encoding="utf-8"))["mapping"]
    assert sorted(mapping.values()) == sorted(m["items"]["executed"])


def test_family_guard_and_provider_pending(tmp_path, monkeypatch):
    with pytest.raises(DraftRunError, match="authoring constraint 1"):
        run_dev_drafts(_cfg(tmp_path, systems=[System.A], llm_backend="anthropic"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    with pytest.raises(DraftRunError, match="PROVIDER NOT CONFIGURED"):
        run_dev_drafts(_cfg(tmp_path, systems=[System.A, System.B], llm_backend="openai", model_id="m-2026-01-01"))
    with pytest.raises(DraftRunError, match="PROVIDER NOT CONFIGURED: GEMINI_API_KEY is not set"):
        run_dev_drafts(_cfg(tmp_path, systems=[System.A, System.A_PLUS, System.B], llm_backend="gemini"))
    assert not (tmp_path / "dev_draft_runs").exists()  # nothing half-written
    cfg = evaluator_configuration(SP, None, None)
    assert cfg["provider"].startswith("gemini") and cfg["model"] == "gemini-3.8-flash"
    assert cfg["API key"].startswith("MISSING") and cfg["settings"]["temperature"] == 0.0


def _replay(tmp_path: Path, items: list[str]) -> Path:
    d = tmp_path / "replay"
    for iid in items:
        ni = build_normalized_input(draft_meta(iid, DESIGN), DRAFTS, DRAFT_UNIT, SP)
        F.write_replay(d, "a_evaluate", ni, [F.body_json(F.record(system="A"))])
        F.write_replay(d, "b_extract", ni, [json.dumps({"events": [], "turn_languages": {
            str(t.turn): "hi-en" for t in ni.turns}})])
        F.write_replay(d, "b_judge", ni, [json.dumps({"answers": [
            {"judgment": "J-G8P", "target": "caller_identifies_org", "answer": "PRESENT", "cited_turns": [1]}]})])
        assert input_sha256(ni)  # fixtures key on content, never on the run alias
    return d


def test_llm_systems_plumbing_and_report(tmp_path):
    items = ["G-02", "K-01"]
    res = run_dev_drafts(_cfg(tmp_path, systems=[System.K0, System.A, System.A_PLUS, System.B], item_ids=items,
                              llm_backend="mock_replay", replay_dir=_replay(tmp_path, items)))
    assert res.completion["n_records_written"] == 8  # K0 x2, A x2, A+ x2, B x2
    rep = build_report(res.run_dir, DESIGN, BENCH / "public", quote_match_min=90.0,
                       evaluator_configuration={"provider": "mock_replay (plumbing test)"})
    assert {k: v["status"] for k, v in rep["systems"].items()} == {s: "EXECUTED" for s in ("K0", "A", "A+", "B")}
    b = rep["systems"]["B"]
    assert b["tokens"]["llm_calls"] == 4 and b["cost"]["usd"] == UNMEASURED  # no price snapshot (B-08)
    assert b["abstention"]["partial"] == 2 and b["abstention"]["inconclusive_checks"] == {"RES-11": 2}
    assert rep["systems"]["A"]["evidence_faithfulness"]["value"] == UNMEASURED  # the stub record cites nothing
    assert rep["systems"]["B"]["pair_accuracy"]["value"] == UNMEASURED  # no complete pair in this subset
    md = render_markdown(rep)
    assert "Reproducibility smoke run" in md and "Not run." in md
    assert "## Measured (this DEV run)" in md and "## Pending (not measured" in md
    assert any(x.startswith("B: verdict accuracy") for x in rep["measured_vs_pending"]["measured"])
    assert "final reliability validation" in rep["measured_vs_pending"]["pending"]
    assert "NOT gold" in md and "| B |" in md and "human review pending: **True**" in md
    assert any((res.run_dir / "B" / i / "TRANSCRIPT" / "r1" / "derivation_log.json").exists() for i in items)


def test_consistency_mode_repeats_k0(tmp_path):
    res = run_dev_drafts(_cfg(tmp_path, repetitions=2, consistency=True, item_ids=["C-10", "K-07"]))
    assert res.completion["n_records_written"] == 4
    from ignosis_eval.devbaseline.metrics import load_run

    _, by_system = load_run(res.run_dir)
    assert all(len({r.content_hash() for r in u.records}) == 1 for u in by_system["K0"].values())
    assert frontend_stability(_cfg(tmp_path, item_ids=["C-10"]), 3) == {"C-10": 1}


# ------------------------------------------------------------------------------------------ metrics (synthetic)
def _ref(iid: str, *, verdict="MEETS_BAR", gates=None, codes=None, control=(), pair=None, target=None,
         conf="Sure") -> ItemRef:
    g = {x: "PASS" for x in F.GATES} | (gates or {})
    return ItemRef(iid, "core", verdict, "EVALUABLE", g, dict(codes or {}), {}, list(control), pair, target, "NONE",
                   conf, False, "-")


def _unit(rec) -> Unit:
    return Unit(records=[rec], ni=F.make_ni(SP), latency=[0.01])


def test_metrics_against_intent():
    refs = {"ZZ-P01": _ref("ZZ-P01", pair=("ZZ-MP", "clean"), control=("G1",)),
            "ZZ-P02": _ref("ZZ-P02", verdict="CRITICAL_FAIL", gates={"G1": "FAIL"}, codes={"UND-01": "INDETERMINATE"},
                           pair=("ZZ-MP", "violating"), target="G1")}
    units = {"ZZ-P01": _unit(F.record()),
             "ZZ-P02": _unit(F.record({"G1": "FAIL"}, [F.finding("UND-01", [2], attribution="INDETERMINATE",
                                                                  quote="stub borrower line beta", role="BORROWER")],
                                      verdict="CRITICAL_FAIL", critical_status="SUSPECTED"))}
    m = system_metrics(refs, units, threshold=90.0, prices=None)
    assert m["verdict_accuracy"]["value"] == 1.0 and m["critical_recall"]["value"] == 1.0
    assert m["must_not_fire_precision"]["value"] == 1.0 and m["pair_accuracy"]["value"] == 1.0
    assert (m["defect_precision"]["value"], m["defect_recall"]["value"]) == (1.0, 1.0)
    assert m["evidence_faithfulness"]["value"] == 1.0 and m["attribution_agreement"]["value"] == 1.0
    assert m["consistency"]["value"] == UNMEASURED
    # the clean member fires the target gate: pair wrong, false fire listed, classified as evaluator failure
    units["ZZ-P01"] = _unit(F.record({"G1": "FAIL"}, verdict="CRITICAL_FAIL", critical_status="SUSPECTED"))
    m = system_metrics(refs, units, threshold=90.0, prices=(1.0, 2.0))
    assert m["pair_accuracy"]["value"] == 0.0 and m["gate_false_fires"] == ["ZZ-P01:G1"]
    assert m["must_not_fire_precision"]["value"] == 0.0
    classes = {d["class"] for d in classify(refs["ZZ-P01"], units["ZZ-P01"].records[0], units["ZZ-P01"])}
    assert classes == {"evaluator_failure"}
    refs["ZZ-P01"] = _ref("ZZ-P01", conf="Probable", pair=("ZZ-MP", "clean"))
    assert {d["class"] for d in classify(refs["ZZ-P01"], units["ZZ-P01"].records[0], units["ZZ-P01"])} == {
        "benchmark_ambiguity"}
    assert pair_results(refs, {"ZZ-P01": set(), "ZZ-P02": set()}, {"ZZ-P01": set(), "ZZ-P02": set()})[
        "ZZ-MP"]["inversion"] is False


def test_metrics_unmeasured_without_support():
    m = system_metrics({"ZZ-P01": _ref("ZZ-P01")}, {"ZZ-P01": _unit(F.record())}, threshold=90.0, prices=None)
    assert m["critical_recall"]["value"] == UNMEASURED and m["must_not_fire_precision"]["value"] == UNMEASURED
    assert m["pair_accuracy"]["value"] == UNMEASURED and m["evidence_faithfulness"]["value"] == UNMEASURED
    assert system_metrics({"ZZ-P01": _ref("ZZ-P01")}, {}, threshold=90.0, prices=None) == {"status": "NOT_EXECUTED"}
