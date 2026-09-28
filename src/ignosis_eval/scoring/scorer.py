"""Scorer: gold + Evaluation Records -> item scores, metrics, discordance tables.

Independent of the evaluator by construction: it imports only contracts, integrity (verification),
stats and metrics. It knows nothing about how a record was produced beyond the declared contract.
"""

from __future__ import annotations

import csv
import io
import json
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ignosis_eval.canonical import canonical_json_pretty
from ignosis_eval.contracts.enums import GateStatus, Severity, Verdict
from ignosis_eval.contracts.evidence import check_evidence
from ignosis_eval.contracts.run_manifest import SCORING_FILES
from ignosis_eval.metrics.alignment import ItemRep, align
from ignosis_eval.metrics.definitions import METRICS, compute
from ignosis_eval.metrics.matching import (
    best_finding,
    evidence_coverage,
    gate_status,
    is_detected,
    is_unsupported_finding,
    required_defects,
)
from ignosis_eval.metrics.slices import language_delta, slice_by
from ignosis_eval.scoring.loader import ScoringError, ScoringInputs, load_scoring_inputs
from ignosis_eval.stats.proportion import IntervalPolicy
from ignosis_eval.versions import METRIC_DEFINITIONS_VERSION, SCORER_VERSION, SCORING_MANIFEST_SCHEMA

ITEM_COLUMNS = [
    "run_id", "item_id", "rep", "split", "scenario", "category", "language", "input_mode", "judge_bait",
    "pair_id", "attribution_pair_id", "gold_verdict", "eval_verdict", "verdict_correct", "gold_evaluability",
    "eval_evaluability", "n_gold_critical", "n_critical_detected", "n_gold_major", "n_major_detected",
    "n_findings", "n_unsupported_findings", "n_evidence", "n_evidence_faithful", "n_integrity_violations",
    "integrity_codes", "n_modality_violations", "modality_codes", "gold_dangerous_win", "eval_dangerous_win",
    "gold_clean_loss", "eval_clean_loss", "record_content_hash",
]
DISCORDANCE_COLUMNS = [
    "run_id", "item_id", "rep", "split", "language", "input_mode", "scenario", "category", "judge_bait",
    "unit_type", "unit_id", "discordance", "severity", "gold_value", "evaluator_value", "detail",
]


@dataclass
class ScoringConfig:
    policy: IntervalPolicy = field(default_factory=IntervalPolicy)
    reference_language: str | None = None


@dataclass
class ScoringResult:
    item_rows: list[dict[str, Any]]
    metrics: dict[str, Any]
    discordances: list[dict[str, Any]]


def _v(x) -> str:
    if x is None:
        return ""
    return x.value if hasattr(x, "value") else str(x)


def item_row(run_id: str, ir: ItemRep) -> dict[str, Any]:
    rec, g, c = ir.record, ir.gold, ir.case
    crit, major = required_defects(g, Severity.CRITICAL), required_defects(g, Severity.MAJOR)
    faithful = 0
    if rec is not None and ir.normalized_input is not None:
        faithful = sum(check_evidence(e, ir.normalized_input).faithful for e in rec.evidence)
    return {
        "run_id": run_id, "item_id": ir.item_id, "rep": ir.rep, "split": _v(c.split), "scenario": c.scenario,
        "category": c.category, "language": c.language, "input_mode": _v(ir.input_mode),
        "judge_bait": c.judge_bait, "pair_id": c.pair_id or "", "attribution_pair_id": c.attribution_pair_id or "",
        "gold_verdict": _v(g.expected_verdict), "eval_verdict": _v(ir.verdict),
        "verdict_correct": g.accepts_verdict(ir.verdict),
        "gold_evaluability": _v(g.expected_evaluability.status),
        "eval_evaluability": _v(rec.evaluability.status) if rec else "",
        "n_gold_critical": len(crit), "n_critical_detected": sum(is_detected(rec, d) for d in crit),
        "n_gold_major": len(major), "n_major_detected": sum(is_detected(rec, d) for d in major),
        "n_findings": len(rec.findings) if rec else 0,
        "n_unsupported_findings": sum(is_unsupported_finding(f, g) for f in rec.findings) if rec else 0,
        "n_evidence": len(rec.evidence) if rec else 0, "n_evidence_faithful": faithful,
        "n_integrity_violations": len(ir.integrity),
        "integrity_codes": ";".join(sorted({str(v.code) for v in ir.integrity})),
        "n_modality_violations": len(ir.modality),
        "modality_codes": ";".join(sorted({str(v.code) for v in ir.modality})),
        "gold_dangerous_win": _v(g.expected_dangerous_win), "eval_dangerous_win": _v(rec.dangerous_win) if rec else "",
        "gold_clean_loss": _v(g.expected_clean_loss), "eval_clean_loss": _v(rec.clean_loss) if rec else "",
        "record_content_hash": rec.content_hash() if rec else "",
    }


def discordances(run_id: str, ir: ItemRep) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    c, g, rec = ir.case, ir.gold, ir.record

    def add(unit_type, unit_id, kind, severity, gold_value, eval_value, detail=""):
        rows.append({"run_id": run_id, "item_id": ir.item_id, "rep": ir.rep, "split": _v(c.split),
                     "language": c.language, "input_mode": _v(ir.input_mode), "scenario": c.scenario,
                     "category": c.category, "judge_bait": c.judge_bait, "unit_type": unit_type,
                     "unit_id": unit_id, "discordance": kind, "severity": severity,
                     "gold_value": _v(gold_value), "evaluator_value": _v(eval_value), "detail": detail})

    for v in ir.integrity:
        add("integrity", str(v.code), "integrity_violation", "critical", "", "", v.detail)
    for v in ir.modality:
        add("modality", str(v.code), "modality_violation", "major", "", "", v.detail)

    if not g.accepts_verdict(ir.verdict):
        gv, ev = g.expected_verdict, ir.verdict
        if ev is Verdict.PASS and gv in (Verdict.INCONCLUSIVE, Verdict.OUT_OF_SCOPE):
            kind, sev = "unsupported_pass", "critical"
        elif ev is Verdict.PASS and gv is Verdict.FAIL:
            kind, sev = "missed_fail", "critical"
        elif ev in (Verdict.INCONCLUSIVE, Verdict.OUT_OF_SCOPE) and gv in (Verdict.PASS, Verdict.FAIL):
            kind, sev = "unwarranted_abstention", "major"
        elif gv in (Verdict.INCONCLUSIVE, Verdict.OUT_OF_SCOPE) and ev in (Verdict.INCONCLUSIVE, Verdict.OUT_OF_SCOPE):
            kind, sev = "wrong_abstention_type", "minor"
        elif ev is None:
            kind, sev = "missing_record", "critical"
        else:
            kind, sev = "verdict_mismatch", "major"
        add("verdict", "verdict", kind, sev, gv, ev)

    for eg in g.expected_gates:
        st = gate_status(rec, eg.gate_id)
        if eg.accepts(st):
            continue
        if st is GateStatus.PASS and eg.status is GateStatus.INCONCLUSIVE:
            kind, sev = "unsupported_gate_pass", "critical"
        elif st is GateStatus.FAIL:
            kind, sev = "critical_false_positive", "critical"
        elif eg.status is GateStatus.FAIL:
            kind, sev = "missed_gate_fail", "critical"
        else:
            kind, sev = "gate_mismatch", "major"
        add("gate", eg.gate_id, kind, sev, eg.status, st or "absent")

    for d in g.expected_defects:
        hit = is_detected(rec, d)
        if d.required and not hit:
            add("defect", d.defect_id, f"{d.severity.value}_miss", d.severity.value, "present", "not_detected")
            continue
        if not hit or rec is None:
            continue
        cov, req = evidence_coverage(rec, d)
        if req and cov < req:
            add("evidence", d.defect_id, "incomplete_evidence", "minor", f"{req} units", f"{cov} covered")
        f = best_finding(rec, d)
        if f is not None and f.attribution.target is not d.attribution:
            kind = "attribution_mismatch" if d.attribution_determinable else "unjustified_attribution"
            add("attribution", d.defect_id, kind, "major", d.attribution, f.attribution.target)
    if rec is not None:
        for f in rec.findings:
            if is_unsupported_finding(f, g):
                add("defect", f.defect_id, "unsupported_defect", f.severity.value, "absent", "reported", f.finding_id)
        if ir.normalized_input is not None:
            for e in rec.evidence:
                chk = check_evidence(e, ir.normalized_input)
                if not chk.faithful:
                    add("evidence", e.evidence_id, "unfaithful_evidence", "major", "", "", chk.reason or "")
    for label, gold_val, eval_val in (
        ("dangerous_win", g.expected_dangerous_win, rec.dangerous_win if rec else None),
        ("clean_loss", g.expected_clean_loss, rec.clean_loss if rec else None),
    ):
        if gold_val is not None and eval_val != gold_val:
            add("outcome", label, f"{label}_mismatch", "major" if label == "dangerous_win" else "minor",
                gold_val, "null" if eval_val is None else eval_val)
    return rows


def build_item_reps(inputs: ScoringInputs) -> list[ItemRep]:
    irs = []
    for (item_id, rep), raw in sorted(inputs.outputs.items()):
        irs.append(align(item_id, rep, inputs.cases[item_id], inputs.gold[item_id], raw, inputs.profile))
    return irs


def score_item_reps(irs: list[ItemRep], config: ScoringConfig, run_id: str = "") -> ScoringResult:
    policy = config.policy
    metrics = {name: compute(name, irs, policy).to_dict() for name in METRICS}
    doc: dict[str, Any] = {
        "metrics": metrics,
        "slices": {
            "by_input_mode": slice_by(irs, lambda ir: ir.input_mode.value, policy),
            "by_split": slice_by(irs, lambda ir: ir.case.split.value, policy),
            "by_judge_bait": slice_by(irs, lambda ir: "judge_bait" if ir.case.judge_bait else "not_judge_bait", policy),
        },
        "language_delta": language_delta(irs, policy, config.reference_language),
        "counts": {
            "items": len({ir.item_id for ir in irs}),
            "repetitions": len({ir.rep for ir in irs}),
            "item_reps": len(irs),
            "valid_records": sum(ir.record is not None for ir in irs),
        },
    }
    rows = [item_row(run_id, ir) for ir in irs]
    disc = [row for ir in irs for row in discordances(run_id, ir)]
    return ScoringResult(rows, doc, disc)


def _csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _write_once(path: Path, text: str) -> None:
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(text)
    path.chmod(0o444)


def score_run(
    run_dir: str | Path, benchmark_root: str | Path, *, scoring_root: str | Path = "scoring",
    config: ScoringConfig | None = None, profile_path: str | Path | None = None, allow_incomplete: bool = False,
    out_suffix: str | None = None,
) -> tuple[Path, ScoringResult]:
    config = config or ScoringConfig()
    inputs = load_scoring_inputs(run_dir, benchmark_root, profile_path=profile_path,
                                 allow_incomplete=allow_incomplete)
    run_id = inputs.manifest.run_id
    out_dir = Path(scoring_root) / (run_id if not out_suffix else f"{run_id}--{out_suffix}")
    if out_dir.exists():
        raise ScoringError(f"{out_dir} already exists; scoring outputs are append-only "
                           "(pass a new --suffix to re-score)")
    irs = build_item_reps(inputs)
    result = score_item_reps(irs, config, run_id)

    m = inputs.manifest
    warnings = []
    if m.profile.status == "placeholder":
        warnings.append("Evaluation profile is a PLACEHOLDER: these numbers are not reportable results.")
    if m.evaluator.llm_backend in ("mock", "none") and m.evaluator.architecture.value in ("A", "A+", "B"):
        warnings.append(f"Evaluator {m.evaluator.name} ran on the '{m.evaluator.llm_backend}' backend: "
                        "results measure pipeline plumbing, not evaluator quality.")
    if m.evaluator.llm_backend == "mock":
        warnings.append("Mock LLM backend: outputs are deterministic heuristics, not model judgements.")
    if not m.official:
        warnings.append("Non-official run (see docs/experiment-protocol.md for official-run requirements).")
    fixture_items = sorted(i for i, c in inputs.cases.items() if "test_fixture" in c.tags)
    if fixture_items:
        warnings.append(f"{len(fixture_items)} item(s) are tagged test_fixture: synthetic test data, not benchmark "
                        "cases.")
    if "provisional" in METRIC_DEFINITIONS_VERSION:
        warnings.append(f"Metric definitions are provisional ({METRIC_DEFINITIONS_VERSION}) pending reconciliation "
                        "with the Stage 4 Reliability Specification.")
    metrics_doc = {
        "schema": "metrics/1.0.0",
        "run_id": run_id,
        "scorer_version": SCORER_VERSION,
        "metric_definitions_version": METRIC_DEFINITIONS_VERSION,
        "provisional_definitions": True,
        "dataset": {"name": m.dataset.name, "version": m.dataset.version, "split": m.dataset.split.value},
        "gold_version": m.gold.gold_version if m.gold else None,
        "evaluator": {"name": m.evaluator.name, "version": m.evaluator.version,
                      "architecture": m.evaluator.architecture.value, "backend": m.evaluator.llm_backend},
        "interval_policy": config.policy.to_dict(),
        "warnings": warnings,
        **result.metrics,
    }
    scoring_manifest = {
        "schema_version": SCORING_MANIFEST_SCHEMA,
        "run_id": run_id,
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "scorer_version": SCORER_VERSION,
        "metric_definitions_version": METRIC_DEFINITIONS_VERSION,
        "run_manifest_sha256": inputs.manifest_sha256,
        "benchmark_manifest_sha256": inputs.benchmark_sha256,
        "gold_manifest_sha256": inputs.gold_manifest_sha256,
        "profile_sha256": inputs.profile_sha256,
        "config": {"interval_policy": config.policy.to_dict(), "reference_language": config.reference_language},
        "allow_incomplete": allow_incomplete,
        "python": platform.python_version(),
        "outputs": list(SCORING_FILES),
    }
    out_dir.mkdir(parents=True, exist_ok=False)
    _write_once(out_dir / "item_scores.csv", _csv(result.item_rows, ITEM_COLUMNS))
    _write_once(out_dir / "discordance_tables.csv", _csv(result.discordances, DISCORDANCE_COLUMNS))
    _write_once(out_dir / "metrics.json", canonical_json_pretty(json.loads(json.dumps(metrics_doc, default=str))))
    _write_once(out_dir / "scoring_manifest.json", canonical_json_pretty(scoring_manifest))
    return out_dir, result


__all__ = ["ScoringConfig", "ScoringError", "ScoringResult", "build_item_reps", "discordances", "item_row",
           "score_item_reps", "score_run"]
