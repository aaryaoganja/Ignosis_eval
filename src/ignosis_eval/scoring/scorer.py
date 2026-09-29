"""Scorer — scoring-spec SD-01 … SD-31 on a blinded view (P-10), write-once outputs (P-11).

    scoring/<run_id>[__<suffix>]/item_scores.csv  metrics.json  discordance_tables.csv  human_checks.csv
                                 scoring_manifest.json

The scorer never imports evaluator code and sees aliases only (SYS-n). Every metric is computed per alias;
the reveal (runner/blind.py) happens after the report exists and its hash is known. Human checks (P-13) are
emitted as PENDING rows; supplying their results (a JSON file of confirmations/ratings) and re-scoring with
a new suffix folds them into H1 and SD-15.
"""

from __future__ import annotations

import csv
import io
import itertools
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ignosis_eval.benchmark.layout import BenchLayout
from ignosis_eval.canonical import canonical_json_pretty, sha256_bytes
from ignosis_eval.contracts.io import read_json
from ignosis_eval.contracts.run_manifest import ScoringManifest
from ignosis_eval.golddrv.capability import CapabilityTable
from ignosis_eval.golddrv.derive import derive_mode_gold
from ignosis_eval.integrity.hashing import file_canonical_sha256
from ignosis_eval.metrics.compute import ScoreCtx, SystemView, public, score_system, tier_vector
from ignosis_eval.metrics.definitions import METRICS
from ignosis_eval.metrics.majority import UnitAgg, dw_majority
from ignosis_eval.metrics.slices import UnitCtx, primary
from ignosis_eval.scoring.loader import ScoringError, ScoringInputs, load_for_scoring
from ignosis_eval.spec.loader import Spec
from ignosis_eval.stats.intervals import sign_test_two_sided
from ignosis_eval.versions import (
    GOLD_DERIVATION_VERSION,
    METRIC_DEFINITIONS_VERSION,
    SCORER_VERSION,
)

DISCORDANCE_METRICS = {
    "sd07": "critical detection (majority fired) on P",
    "sd08": "no targeted false fire on controls",
    "sd09": "no unsupported pass (gold INCONCLUSIVE / OUT_OF_SCOPE gates)",
    "sd10": "abstention target correct",
    "sd12": "gold code matched in a majority of reps",
    "sd17": "majority verdict = gold verdict",
    "sd20": "pair member pair correct",
}


def _csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})
    return buf.getvalue()


def _write_once(path: Path, text: str) -> str:
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(text)
    except FileExistsError as exc:
        raise ScoringError(f"refusing to overwrite {path}; re-score with a new --suffix") from exc
    path.chmod(0o444)
    return sha256_bytes(text.encode("utf-8"))


def audit_h7(inp: ScoringInputs) -> dict[str, Any]:
    h = inp.view.h7
    if h.kind in ("dev", "dev_tuning"):
        return {"status": "NOT_APPLICABLE (dev run)", "locked": h.locked}
    hv = h.hash_verification
    checks = {
        "locked": h.locked,
        "tagged_clean_commit": bool(h.git_tag) and not h.git_dirty,
        "hashes_verified": all([hv.bench_manifest, hv.item_files, hv.private_hash_list is True, hv.gold, hv.rubric,
                                hv.profile, hv.lexicons]),
        "verified_before_run": hv.verified_at <= h.run_created_at,
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", **checks, "git_tag": h.git_tag}


def build_units(inp: ScoringInputs, spec: Spec) -> list[UnitCtx]:
    table = CapabilityTable.from_rubric(spec.rubric)
    units = []
    for uid, f in sorted(inp.facts.items()):
        entry = inp.bench.item(f.item_id)
        assert entry is not None
        if entry.meta.scoring_role != "scored":
            continue  # snippets are SD-22 component tests; calibration is never scored
        units.append(UnitCtx(uid, entry.meta, f, derive_mode_gold(inp.gold[f.item_id], f, spec.rubric, table)))
    return units


# Claims the benchmark cannot support, stated with every metrics.json (BD-02, frozen-contract §0 / §12).
UNMEASURED = {
    "G7_recall": {"status": "UNMEASURED",
                  "reason": "BD-02: bench-a1 contains no G7 positive. G7 recall is not measured by the benchmark "
                            "and is covered by deterministic unit tests only (tests/test_frontend.py)."},
}


def _warnings(inp: ScoringInputs, units: list[UnitCtx], systems: dict[str, dict[str, Any]]) -> list[str]:
    v = inp.view
    w = ["No production-accuracy claims (SD-29 rule 7).",
         "G7 recall is UNMEASURED: bench-a1 contains no G7 positive (BD-02); any G7 detection figure here is not a "
         "recall estimate."]
    if v.dataset.split.value == "dev":
        w.append("DEV SPLIT: tuning / debugging data. Not a reliability result; never compare against holdout.")
    if v.mock_backend:
        w.append("MOCK LLM BACKEND (replayed fixtures): these numbers measure plumbing only, not evaluator quality.")
    if not v.spec.profile_is_canonical:
        w.append("NON-CANONICAL PROFILE (e.g. a test profile with placeholder values): plumbing only.")
    if v.pending_signoff:
        w.append(f"{len(v.pending_signoff)} PENDING_HUMAN_SIGNOFF item(s) unresolved: "
                 + ", ".join(sorted({p.blocker or p.path for p in v.pending_signoff})))
    if any(not u.meta.synthetic for u in units):
        w.append("Real (non-synthetic) calls present: report them as a separate stratum (B-16).")
    if any(s["sd11"]["pending_confirmation"] or s["sd15"]["status"] != "rated" for s in systems.values()):
        w.append("Human checks pending (P-13): H1 textual candidates and/or SD-15 evidence-support ratings.")
    if any(u.gold.requires_human_spot_check for u in units):
        w.append("Mode-derived gold of audio units awaits the per-item human spot check (P-12).")
    return w


def _scope_line(inp: ScoringInputs, spec: Spec, units: list[UnitCtx]) -> str:
    kind = "synthetic calls" if all(u.meta.synthetic for u in units) else "synthetic and real calls"
    model = ", ".join(inp.view.model_snapshot_ids) or "none"
    return (f"{inp.view.dataset.name}, {kind}, profile {spec.profile_id}, rubric {spec.rubric_version}, "
            f"model {model}")


def _item_rows(alias: str, units: list[UnitCtx], aggs: dict[str, UnitAgg], gates: tuple[str, ...],
               primary_ids: set[str]) -> list[dict[str, Any]]:
    rows = []
    for u in units:
        a = aggs[u.unit_id]
        rows.append({
            "system": alias, "unit_id": u.unit_id, "item_id": u.item_id, "unit_mode": u.mode, "pack": u.pack,
            "primary_universe": u.unit_id in primary_ids, "gold_verdict": u.gold.verdict,
            "majority_verdict": a.verdict, "verdict_correct": a.verdict == u.gold.verdict,
            "reps": a.k, "reps_failed": a.n_failed,
            "gold_fail_gates": ";".join(sorted(u.gold.fail_gates())),
            "majority_fired_gates": ";".join(sorted(a.fired_set())),
            "detected_counts": ";".join(f"{g}:{a.gate(g).detected}" for g in gates if a.gate(g).detected),
            "consistent": a.consistent(), "reps_holding_most_frequent_verdict": a.reps_holding_most_frequent_verdict(),
            "gold_dangerous_win": u.gold.dangerous_win, "majority_dangerous_win": dw_majority(a.reps),
            "gold_clean_loss": u.gold.clean_loss, "majority_clean_loss": a.clean_loss(),
            "gold_derivation_notes": " | ".join(u.gold.notes),
        })
    return rows


def _discordance(outcomes: dict[str, dict[str, dict[str, bool]]]) -> list[dict[str, Any]]:
    rows = []
    aliases = sorted(outcomes)
    for metric, label in DISCORDANCE_METRICS.items():
        for s1, s2 in itertools.combinations(aliases, 2):
            o1, o2 = outcomes[s1].get(metric, {}), outcomes[s2].get(metric, {})
            keys = sorted(set(o1) & set(o2))
            b = sum(o1[k] and not o2[k] for k in keys)
            c = sum(o2[k] and not o1[k] for k in keys)
            rows.append({"metric": metric, "definition": label, "system_1": s1, "system_2": s2, "n": len(keys),
                         "both_right": sum(o1[k] and o2[k] for k in keys),
                         "both_wrong": sum(not o1[k] and not o2[k] for k in keys),
                         "s1_right_s2_wrong": b, "s1_wrong_s2_right": c,
                         "sign_test_p_two_sided": round(sign_test_two_sided(b, c), 6),
                         "note": "reported, not decisive (P-15 step 5)"})
    return rows


def _human_rows(inp: ScoringInputs, units: list[UnitCtx], systems: dict[str, dict[str, Any]],
                aggs_by_alias: dict[str, dict[str, UnitAgg]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for alias, s in systems.items():
        for r in s["_human"]["evidence_support"]:
            rows.append({"check_id": r["id"], "check_type": "evidence_support", "system_alias": alias,
                         "unit_id": r["unit_id"], "rep": 1, "check": r["check"], "detail": r["kind"],
                         "text": r["quotes"], "status": r["rating"],
                         "allowed_values": "SUPPORTS|PARTIAL|DOES_NOT_SUPPORT"})
        for c in s["_human"]["external_truth"]:
            rows.append({"check_id": c["id"], "check_type": "external_truth_candidate", "system_alias": alias,
                         "unit_id": c["unit_id"], "rep": c["rep"], "check": f"SD-11 pattern {c['pattern']}",
                         "detail": f"{c['field']}: {c['match']}", "text": c["text"], "status": c["result"],
                         "allowed_values": "CONFIRMED|REJECTED"})
        for u in units:
            for g, gg in u.gold.gates.items():
                if gg.contested:
                    m = aggs_by_alias[alias][u.unit_id].gate(g)
                    rows.append({"check_id": f"CT:{alias}:{u.unit_id}:{g}", "check_type": "contested_review",
                                 "system_alias": alias, "unit_id": u.unit_id, "rep": "", "check": g,
                                 "detail": f"gold {gg.status}; majority {m.status}; detected {m.detected}",
                                 "text": "", "status": "PENDING", "allowed_values": "free text"})
    for u in units:
        if u.gold.requires_human_spot_check:
            rows.append({"check_id": f"MG:{u.unit_id}", "check_type": "mode_gold_spot_check", "system_alias": "",
                         "unit_id": u.unit_id, "rep": "", "check": "mode-derived gold",
                         "detail": " | ".join(u.gold.notes) or "no derivation notes", "text": "", "status": "PENDING",
                         "allowed_values": "AGREE|DISAGREE"})
    return rows


HUMAN_COLUMNS = ["check_id", "check_type", "system_alias", "unit_id", "rep", "check", "detail", "text", "status",
                 "allowed_values"]
ITEM_COLUMNS = ["system", "unit_id", "item_id", "unit_mode", "pack", "primary_universe", "gold_verdict",
                "majority_verdict", "verdict_correct", "reps", "reps_failed", "gold_fail_gates", "majority_fired_gates",
                "detected_counts", "consistent", "reps_holding_most_frequent_verdict", "gold_dangerous_win",
                "majority_dangerous_win", "gold_clean_loss", "majority_clean_loss", "gold_derivation_notes"]
DISCORDANCE_COLUMNS = ["metric", "definition", "system_1", "system_2", "n", "both_right", "both_wrong",
                       "s1_right_s2_wrong", "s1_wrong_s2_right", "sign_test_p_two_sided", "note"]


def score_view(view_dir: str | Path, layout: BenchLayout, spec: Spec, scoring_root: str | Path, *,
               suffix: str | None = None, confirmations_path: str | Path | None = None,
               sample_seed: int | None = None) -> Path:
    inp = load_for_scoring(view_dir, layout, spec)
    units = build_units(inp, spec)
    confirmations = read_json(confirmations_path) if confirmations_path else {}
    seed = inp.view.base_seed if sample_seed is None else sample_seed
    ctx = ScoreCtx(rubric=spec.rubric, registry=spec.registry, table=CapabilityTable.from_rubric(spec.rubric),
                   quote_match_min=float(spec.threshold("quote_match_min")), registries=inp.registries,
                   split=inp.view.dataset.split.value, sample_seed=seed, confirmations=confirmations)
    h7 = audit_h7(inp)
    systems: dict[str, dict[str, Any]] = {}
    aggs_by_alias: dict[str, dict[str, UnitAgg]] = {}
    for alias in inp.view.aliases:
        aggs = {u.unit_id: UnitAgg(inp.reps[(alias, u.unit_id)], ctx.gates) for u in units}
        nis = {(u_id, r): ni for (a, u_id, r), ni in inp.nis.items() if a == alias}
        aggs_by_alias[alias] = aggs
        systems[alias] = score_system(ctx, units, SystemView(alias, aggs, nis), locked_audit=h7)

    run_id = inp.view.run_id
    out_dir = Path(scoring_root) / (f"{run_id}__{suffix}" if suffix else run_id)
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ScoringError(f"{out_dir} exists; scoring outputs are write-once (use --suffix)") from exc

    primary_ids = {u.unit_id for u in primary(units, ctx.split)}
    item_rows = [row for alias in inp.view.aliases
                 for row in _item_rows(alias, units, aggs_by_alias[alias], ctx.gates, primary_ids)]
    outcomes = {a: {k: s[k]["_outcomes"] for k in DISCORDANCE_METRICS} for a, s in systems.items()}
    order = sorted(systems, key=lambda a: tier_vector(systems[a]["tiers"]))
    metrics = {
        "scope_line": _scope_line(inp, spec, units),
        "warnings": _warnings(inp, units, systems),
        "run_id": run_id, "split": ctx.split, "aliases": inp.view.aliases,
        "scorer_version": SCORER_VERSION, "metric_definitions_version": METRIC_DEFINITIONS_VERSION,
        "gold_derivation_version": GOLD_DERIVATION_VERSION,
        "definitions": {k: {"section": v[0], "description": v[1]} for k, v in METRICS.items()},
        "systems": {a: public(s) for a, s in systems.items()},
        "hard_requirements": {a: {h: v["status"] for h, v in s["hard_requirements"].items()} for a, s in systems.items()},
        "tier_vectors": {a: list(tier_vector(s["tiers"])) for a, s in systems.items()},
        "tier_order_lexicographic": order,
        "tier_note": "S0..S10 compared lexicographically (lower is safer); P-15's simplicity preference and "
                     "'meaningful reduction' rule apply after the reveal and are not applied here.",
        "unmeasured": UNMEASURED,
    }
    outputs = {
        "item_scores.csv": _write_once(out_dir / "item_scores.csv", _csv(item_rows, ITEM_COLUMNS)),
        "metrics.json": _write_once(out_dir / "metrics.json", canonical_json_pretty(metrics)),
        "discordance_tables.csv": _write_once(out_dir / "discordance_tables.csv",
                                              _csv(_discordance(outcomes), DISCORDANCE_COLUMNS)),
        "human_checks.csv": _write_once(out_dir / "human_checks.csv",
                                        _csv(_human_rows(inp, units, systems, aggs_by_alias), HUMAN_COLUMNS)),
    }
    ds = inp.view.dataset
    sm = ScoringManifest(
        run_id=run_id, scoring_id=out_dir.name, scorer_version=SCORER_VERSION,
        metric_definitions_version=METRIC_DEFINITIONS_VERSION, gold_derivation_version=GOLD_DERIVATION_VERSION,
        view_manifest_sha256=inp.view_sha256, bench_manifest_sha256=ds.bench_manifest_sha256,
        gold_manifest_sha256=inp.gold_manifest_sha256, registries_sha256=ds.registries_sha256,
        rubric_sha256=spec.rubric_sha256, profile_sha256=spec.profile_sha256,
        human_confirmations_sha256=file_canonical_sha256(Path(confirmations_path)) if confirmations_path else None,
        sample_seed=seed, created_at=datetime.now(timezone.utc), outputs=outputs)
    _write_once(out_dir / "scoring_manifest.json", canonical_json_pretty(sm.model_dump(mode="json")))
    return out_dir
