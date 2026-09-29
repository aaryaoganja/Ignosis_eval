"""Metric computation — scoring-spec SD-06 … SD-27, SD-30 (normative). One system at a time.

Inputs are the scorer's view only: per-rep observations of the evaluation records (aliases, blinded),
mode-derived gold, registries, rubric/profile values and the unit capability facts. Nothing here knows
which system an alias stands for.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.registries import Registries
from ignosis_eval.golddrv.capability import CapabilityTable, perception_allowed
from ignosis_eval.metrics.alignment import NO_MAJORITY_COLUMN, SD06, SD06_COLUMNS, SD06_ROWS, eval_column, gold_row
from ignosis_eval.metrics.external_truth import structural_violations, text_candidates
from ignosis_eval.metrics.majority import NO_MAJORITY, UnitAgg, dw_majority
from ignosis_eval.metrics.matching import (
    SEVERITY_RANK,
    completeness,
    evaluator_severity,
    faithful,
    gate_matched,
    match_code,
    matching_finding,
    quotes,
    required_elements,
)
from ignosis_eval.metrics.slices import UnitCtx, all_scored, audio_by_mode, primary, twins
from ignosis_eval.spec.registry import Registry
from ignosis_eval.stats.proportion import rate

VERDICT_ORDER = {"CRITICAL_FAIL": 3, "NEEDS_ATTENTION": 2, "MEETS_BAR": 1}
SURE = "Sure"


@dataclass
class ScoreCtx:
    rubric: dict[str, Any]
    registry: Registry
    table: CapabilityTable
    quote_match_min: float
    registries: Registries
    split: str
    sample_seed: int
    confirmations: dict[str, dict[str, str]] = field(default_factory=dict)  # human check id -> result

    @property
    def gates(self) -> tuple[str, ...]:
        return self.registry.gate_ids

    @property
    def codes(self) -> tuple[str, ...]:
        """Non-gate MVP codes including PLT codes."""
        return self.registry.code_ids + self.registry.platform_ids

    def raw(self, check_id: str) -> dict[str, Any]:
        return self.registry.get(check_id).raw

    def in_scope(self, u: UnitCtx, check_id: str) -> bool:
        f = u.facts
        return self.table.in_scope(check_id, f.input_mode.value, has_call_start_ts=f.has_call_start_ts,
                                   has_timestamps=f.has_timestamps,
                                   provenance=f.provenance.value if f.provenance else None)[0]

    def bucket(self, code: str, gold_sev: str | None, eval_sev: str | None) -> str | None:
        """Major / Minor micro-average bucket (§4.1). COM-03 is Major for PTP and Minor for callback, so it
        is classified per (unit, rep) by the gold severity, else the evaluator severity."""
        majors, minors = set(self.registry.major_codes), set(self.registry.minor_codes)
        if code in majors and code in minors:
            sev = gold_sev or eval_sev
            return "major" if sev == "MAJOR" else "minor" if sev == "MINOR" else None
        if code in majors:
            return "major"
        if code in minors:
            return "minor"
        return None


@dataclass
class SystemView:
    alias: str
    aggs: dict[str, UnitAgg]  # unit_id -> majority output
    nis: dict[tuple[str, int], NormalizedInput]  # (unit_id, rep) -> normalized input


def _uid_g(u: UnitCtx, g: str) -> str:
    return f"{u.unit_id}:{g}"


# =========================================================================================== SD-06
def sd06(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    per_rep: dict[str, Counter] = {r: Counter() for r in SD06_ROWS}
    maj: dict[str, Counter] = {r: Counter() for r in SD06_ROWS}
    for u in units:
        agg = sv.aggs[u.unit_id]
        for g in ctx.gates:
            gg = u.gold.gates[g]
            row = gold_row(gg.status, gg.trigger)
            for rep in agg.reps:
                o = rep.gates.get(g) if not rep.failed else None
                col = eval_column(o.status if o else None, o.critical_status if o else None)
                per_rep[row][col] += 1
            m = agg.gate(g)
            col = f"FAIL+{m.critical_status}" if m.status == "FAIL" else m.status  # FAIL <=> fired in >= 3 reps
            maj[row][col] += 1
    cols = list(SD06_COLUMNS) + ["EVALUATION_FAILED"]
    mcols = list(SD06_COLUMNS) + [NO_MAJORITY_COLUMN]
    return {"columns": cols, "labels": {r: list(SD06[r]) + ["evaluation_failed"] for r in SD06_ROWS},
            "per_rep": {r: {c: per_rep[r][c] for c in cols} for r in SD06_ROWS},
            "majority": {r: {c: maj[r][c] for c in mcols} for r in SD06_ROWS},
            "note": "majority column = SD-04 gate majority status (AJ-11): FAIL iff fired in >=3 reps (CONFIRMED iff "
                    "CONFIRMED in >=3 reps), else a status held in >=3 reps, else NO_MAJORITY (never correct)"}


# =========================================================================================== SD-07
def sd07(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    P = [(u, g) for u in units for g in ctx.gates
         if u.gold.gates[g].status == "FAIL" and not u.gold.gates[g].contested]
    k = max((sv.aggs[u.unit_id].k for u in units), default=5)
    thr = k // 2 + 1
    det = {_uid_g(u, g): sv.aggs[u.unit_id].gate(g).detected for u, g in P}
    total = sum(det.values())
    return {
        "P": len(P), "distinct_items": len({u.item_id for u, _ in P}), "k": k,
        "pooled_per_rep_recall": rate(total, k * len(P), clustered=True),
        "pooled_per_rep_misses": k * len(P) - total,
        "stability": rate(sum(d == k for d in det.values()), len(P)),
        "majority_critical_misses": sum(d < thr for d in det.values()),
        "majority_critical_miss_ids": sorted(x for x, d in det.items() if d < thr),
        "flip_to_pass": sorted(x for x, d in det.items() if 1 <= d <= k - 1),
        "detected_counts": dict(sorted(det.items())),
        "_outcomes": {x: d >= thr for x, d in det.items()},
    }


# =========================================================================================== SD-08
def sd08(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    """SD-08 (SC-04): targeted controls have gold PASS or NA on their target gates (the trigger is often absent
    by design); a fire on either is a false fire."""
    C = [(u, g.value) for u in units for g in ctx.registries.control_targets(u.item_id)
         if u.gold.gates[g.value].status in ("PASS", "NA") and not u.gold.gates[g.value].contested]
    fired = {_uid_g(u, g): sv.aggs[u.unit_id].gate(g) for u, g in C}
    targeted = sorted(x for x, m in fired.items() if m.fired)
    confirmed = sorted(x for x, m in fired.items() if m.fired and m.critical_status == "CONFIRMED")
    glob = [(u, g) for u in units for g in ctx.gates
            if u.gold.gates[g].status in ("PASS", "NA") and not u.gold.gates[g].contested]
    gfires = [_uid_g(u, g) for u, g in glob if sv.aggs[u.unit_id].gate(g).fired]
    return {
        "C": len(C), "targeted_false_fires": len(targeted), "targeted_false_fire_ids": targeted,
        "targeted_false_fire_rate": rate(len(targeted), len(C)),
        "confirmed_only_targeted_false_fires": len(confirmed),
        "suspected_only_targeted_fires": rate(len(targeted) - len(confirmed), len(C)),
        "global_false_fires": rate(len(gfires), len(glob)), "global_false_fire_ids": sorted(gfires),
        "_outcomes": {x: not m.fired for x, m in fired.items()},
    }


# =========================================================================================== SD-09
def sd09(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    unsupported: list[str] = []
    overclaim: list[str] = []
    unsup_defect: list[str] = []
    eligible_up: dict[str, bool] = {}
    per_rep_up = per_rep_n = 0
    for u in units:
        agg = sv.aggs[u.unit_id]
        for g in ctx.gates:
            gg, m = u.gold.gates[g], agg.gate(g)
            if gg.status in ("INCONCLUSIVE", "OUT_OF_SCOPE"):
                bad = agg.gate_in(g, ("PASS", "NA"))  # majority `passed` (SD-04 indicator PASS or NA)
                eligible_up[_uid_g(u, g)] = not bad
                if bad:
                    unsupported.append(_uid_g(u, g))
                per_rep_n += agg.k  # SD-09: per-rep unsupported passes, pooled (an EF rep is not a pass)
                per_rep_up += sum(1 for r in agg.reps if not r.failed and r.gates[g].status in ("PASS", "NA"))
            if gg.status == "INCONCLUSIVE" and m.fired and m.critical_status == "CONFIRMED":
                (overclaim if gg.trigger else unsup_defect).append(_uid_g(u, g))
        for c in ctx.codes:
            if u.gold.codes[c].status == "INCONCLUSIVE" and agg.code_emitted(c):
                unsup_defect.append(_uid_g(u, c))
    unit_elig = [u for u in units if u.gold.verdict != "NOT_EVALUABLE"]
    unit_over = [u.unit_id for u in unit_elig if sv.aggs[u.unit_id].verdict == "NOT_EVALUABLE"]
    check_elig, check_over = 0, []
    for u in units:
        agg = sv.aggs[u.unit_id]
        if agg.verdict == "NOT_EVALUABLE":
            continue
        for g in ctx.gates:
            gg = u.gold.gates[g]
            if ctx.in_scope(u, g) and gg.status in ("PASS", "FAIL") and gg.label_confidence == SURE:
                check_elig += 1
                if agg.gate_in(g, ("INCONCLUSIVE", "OUT_OF_SCOPE")):
                    check_over.append(_uid_g(u, g))
        for c in ctx.codes:
            cg = u.gold.codes[c]
            if ctx.in_scope(u, c) and cg.status in ("PASS", "DEFECT") and cg.label_confidence == SURE:
                check_elig += 1
                if agg.code_in(c, ("INCONCLUSIVE", "OUT_OF_SCOPE")):
                    check_over.append(_uid_g(u, c))
    return {
        "unsupported_passes": len(unsupported), "unsupported_pass_ids": sorted(unsupported),
        "unsupported_pass_eligible": len(eligible_up),
        "unsupported_passes_pooled_per_rep": rate(per_rep_up, per_rep_n, clustered=True),
        "overclaims": len(overclaim), "overclaim_ids": sorted(overclaim),
        "unsupported_defects": len(unsup_defect), "unsupported_defect_ids": sorted(unsup_defect),
        "unit_over_abstention": rate(len(unit_over), len(unit_elig)), "unit_over_abstention_ids": sorted(unit_over),
        "check_over_abstention": rate(len(check_over), check_elig, clustered=True),
        "check_over_abstention_ids": sorted(check_over),
        "_outcomes": eligible_up,
    }


# =========================================================================================== SD-10
def sd10(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    correct, total, detail = 0, 0, {}
    for u in units:
        agg = sv.aggs[u.unit_id]
        for check, expected in u.gold.abstention_targets:
            total += 1
            if expected == "NOT_EVALUABLE":
                ok = agg.verdict == "NOT_EVALUABLE"
            elif expected == "SUSPECTED":
                ok = check in ctx.gates and agg.gate(check).suspected >= agg.thr
            else:
                ok = agg.gate_in(check, (expected,)) if check in ctx.gates else agg.code_in(check, (expected,))
            correct += ok
            detail[f"{u.unit_id}:{check}:{expected}"] = ok
    abst, abst_ok = 0, 0
    for u in units:
        agg = sv.aggs[u.unit_id]
        for c in list(ctx.gates) + list(ctx.codes):
            if c in ctx.registry.platform_ids and not ctx.in_scope(u, c):
                continue  # checks = gates + MVP codes + applicable PLT codes (SD-09 definition)
            abstained = agg.gate_in(c, ("INCONCLUSIVE", "OUT_OF_SCOPE")) if c in ctx.gates else \
                agg.code_in(c, ("INCONCLUSIVE", "OUT_OF_SCOPE"))
            if abstained:
                abst += 1
                abst_ok += u.gold.check_status(c) in ("INCONCLUSIVE", "OUT_OF_SCOPE")
    return {"T": total, "abstention_recall": rate(correct, total), "abstention_precision": rate(abst_ok, abst,
                                                                                             clustered=True),
            "incorrect_targets": sorted(k for k, v in detail.items() if not v), "_outcomes": detail}


# =========================================================================================== SD-11
def sd11(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    oos = frozenset(ctx.registry.oos_codes)
    structural, candidates = [], []
    for u in units:
        for rep in sv.aggs[u.unit_id].reps:
            if rep.failed or rep.record is None:
                continue
            for v in structural_violations(rep.record, oos_codes=oos, has_call_start_ts=u.facts.has_call_start_ts):
                structural.append(f"{u.unit_id}:r{rep.rep}:{v}")
            for i, c in enumerate(text_candidates(rep.record)):
                cid = f"ET:{sv.alias}:{u.unit_id}:r{rep.rep}:{i}"
                candidates.append({"id": cid, "unit_id": u.unit_id, "rep": rep.rep, "field": c.field,
                                   "pattern": c.pattern, "match": c.match, "text": c.text,
                                   "result": ctx.confirmations.get(cid, {}).get("result", "PENDING")})
    confirmed = sum(c["result"] == "CONFIRMED" for c in candidates)
    pending = sum(c["result"] == "PENDING" for c in candidates)
    return {"structural_violations": len(structural), "structural_ids": structural,
            "textual_candidates": len(candidates), "confirmed": confirmed, "pending_confirmation": pending,
            "h1_count": len(structural) + confirmed, "_candidates": candidates}


# =========================================================================================== SD-12
def sd12(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    per_code: dict[str, Counter] = {c: Counter() for c in ctx.codes}
    buckets: dict[str, Counter] = {"major": Counter(), "minor": Counter()}
    bi: Counter[str] = Counter()
    sev_mismatch: list[str] = []
    outcomes: dict[str, bool] = {}
    for u in units:
        agg = sv.aggs[u.unit_id]
        for c in ctx.codes:
            cg = u.gold.codes[c]
            anchors = cg.anchor_turns if cg.status == "DEFECT" else ()
            for rep in agg.reps:
                m = match_code(rep, c, anchors)
                if not (m.tp or m.fp or m.fn):
                    continue
                esev = evaluator_severity(rep, c)
                for key, flag in (("tp", m.tp), ("fp", m.fp), ("fn", m.fn)):
                    if flag:
                        per_code[c][key] += 1
                        b = ctx.bucket(c, cg.severity if cg.status == "DEFECT" else None, esev)
                        if b:
                            buckets[b][key] += 1
                        if c in ctx.registry.borrower_impact_majors:
                            bi[key] += 1
                if m.tp and esev != cg.severity:
                    sev_mismatch.append(f"{u.unit_id}:{c}:r{rep.rep}:{esev}!={cg.severity}")
            if anchors:
                outcomes[_uid_g(u, c)] = sum(match_code(r, c, anchors).tp for r in agg.reps) >= agg.thr

    def pr(cnt: Counter) -> dict[str, Any]:
        return {"tp": cnt["tp"], "fp": cnt["fp"], "fn": cnt["fn"],
                "precision": rate(cnt["tp"], cnt["tp"] + cnt["fp"], clustered=True),
                "recall": rate(cnt["tp"], cnt["tp"] + cnt["fn"], clustered=True)}

    return {"per_code": {c: pr(v) for c, v in per_code.items()}, "major_micro": pr(buckets["major"]),
            "minor_micro": pr(buckets["minor"]), "borrower_impact_major": pr(bi),
            "severity_mismatches": len(sev_mismatch), "severity_mismatch_ids": sev_mismatch,
            "_major": buckets["major"], "_minor": buckets["minor"], "_bi": bi, "_outcomes": outcomes}


# =========================================================================================== SD-13
def sd13(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    n = ok = 0
    h2: list[str] = []
    flagged: list[str] = []
    for u in units:
        for rep in sv.aggs[u.unit_id].reps:
            if rep.failed:
                continue
            ni = sv.nis.get((u.unit_id, rep.rep))
            for q in quotes(rep):
                n += 1
                good, why = faithful(q["evidence"], ni, ctx.quote_match_min) if ni else (False, "no normalized input")
                ok += good
                if not good and q["kind"] == "gate":
                    (flagged if q["flagged"] else h2).append(f"{u.unit_id}:r{rep.rep}:{q['check']}:{why}")
    return {"faithfulness": rate(ok, n, clustered=True), "h2_violations": len(h2), "h2_ids": h2,
            "flagged_unfaithful_gate_quotes": len(flagged), "flagged_ids": flagged}


# =========================================================================================== SD-14 / SD-16
def _tp_objects(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView):
    """Yield (unit, rep, check, cited turns, header cited, evaluator attribution, gold) for TP code findings
    and matched gates, pooled over reps."""
    for u in units:
        for rep in sv.aggs[u.unit_id].reps:
            if rep.failed:
                continue
            for g in ctx.gates:
                gg = u.gold.gates[g]
                if gate_matched(rep, g, gg.status, gg.anchor_turns):
                    o = rep.gates[g]
                    yield u, rep, g, o.turns, o.header_evidence, o.attribution, gg.attribution, gg.evidence_elements
            for c in ctx.codes:
                cg = u.gold.codes[c]
                if cg.status != "DEFECT":
                    continue
                if match_code(rep, c, cg.anchor_turns).tp:
                    turns = sorted({t for f in rep.asserted(c) for t in f.turns})
                    f = matching_finding(rep, c, cg.anchor_turns)
                    yield u, rep, c, tuple(turns), False, f.attribution if f else None, cg.attribution, \
                        cg.evidence_elements


def sd14(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    scores: list[float] = []
    missing_gold = 0
    for u, _rep, check, turns, header, _ea, _ga, elements in _tp_objects(ctx, units, sv):
        dw = u.gold.dangerous_win
        req, missing = required_elements(ctx.raw(check), elements, dw)
        missing_gold += len(missing)
        if not req:
            continue
        sat, n = completeness(turns, header, req, elements)
        scores.append(sat / n)
    return {"n_findings": len(scores), "mean": round(sum(scores) / len(scores), 4) if scores else None,
            "share_complete": rate(sum(s == 1.0 for s in scores), len(scores), clustered=True),
            "required_elements_without_gold_turn": missing_gold}


def sd16(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    n = acc = undetermined = gi = unjust = 0
    for _u, _rep, _check, _t, _h, ev_attr, gold_attr, _e in _tp_objects(ctx, units, sv):
        if gold_attr is None:
            undetermined += 1
            continue
        n += 1
        acc += ev_attr == gold_attr
        if gold_attr == "INDETERMINATE":
            gi += 1
            unjust += ev_attr is not None and ev_attr != "INDETERMINATE"
    return {"accuracy": rate(acc, n, clustered=True), "unjustified": unjust,
            "unjustified_rate": rate(unjust, gi, clustered=True), "gold_attribution_undetermined": undetermined}


# =========================================================================================== SD-17 / 18 / 19
def sd17(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    correct = lenient = strict = unsupported = failed = s3 = no_majority = 0
    per_rep_ok = per_rep_n = wsc_n = wsc_ok = 0
    outcomes, errors = {}, {}
    for u in units:
        agg, gold = sv.aggs[u.unit_id], u.gold.verdict
        v = agg.verdict
        correct += v == gold
        outcomes[u.unit_id] = v == gold
        per_rep_ok += sum(r.verdict == gold for r in agg.reps)
        per_rep_n += agg.k
        if v in VERDICT_ORDER and gold in VERDICT_ORDER:
            if VERDICT_ORDER[v] < VERDICT_ORDER[gold]:
                lenient += 1
                errors[u.unit_id] = "lenient"
                s3 += gold == "CRITICAL_FAIL"
            elif VERDICT_ORDER[v] > VERDICT_ORDER[gold]:
                strict += 1
                errors[u.unit_id] = "strict"
        if gold == "NOT_EVALUABLE" and v != "NOT_EVALUABLE":
            unsupported += 1
            errors[u.unit_id] = "unsupported_evaluation"
        if v == "EVALUATION_FAILED":
            failed += 1
            errors[u.unit_id] = "failed"
        if v == NO_MAJORITY:
            no_majority += 1
            errors.setdefault(u.unit_id, "no_majority")
        if u.gold.within_scope_complete is not None and v not in ("EVALUATION_FAILED", NO_MAJORITY):
            wsc_n += 1
            wsc_ok += agg.within_scope_complete() == u.gold.within_scope_complete
    return {"units": len(units), "accuracy": rate(correct, len(units)),
            "accuracy_pooled_per_rep": rate(per_rep_ok, per_rep_n, clustered=True),
            "lenient_errors": rate(lenient, len(units)), "strict_errors": strict,
            "unsupported_evaluations": unsupported, "failed": failed, "no_majority": no_majority,
            "s3_lenient_on_gold_critical_fail": s3,
            "within_scope_complete_agreement": rate(wsc_ok, wsc_n), "errors": errors, "_outcomes": outcomes}


def critical_status_split(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    """SD-17 (AJ-12), descriptive only: for fired gold-FAIL units (gold FAIL and majority fired), the share whose
    majority critical status is CONFIRMED vs SUSPECTED. There is no gold critical status and no mismatch metric;
    overclaim (SD-09) is the only critical-status error."""
    majority: Counter[str] = Counter()
    for u in units:
        agg = sv.aggs[u.unit_id]
        for g in ctx.gates:
            if u.gold.gates[g].status != "FAIL":
                continue
            m = agg.gate(g)
            if m.fired and m.critical_status:
                majority[m.critical_status] += 1
    n = sum(majority.values())
    return {"fired_gold_fail": n,
            "majority": {k: rate(majority[k], n) for k in ("CONFIRMED", "SUSPECTED")},
            "note": "descriptive only (AJ-12)"}


def sd18(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    dw_n = dw_ok = cl_n = cl_ok = 0
    for u in units:
        agg = sv.aggs[u.unit_id]
        if u.gold.dangerous_win is not None:
            dw_n += 1
            dw_ok += dw_majority(agg.reps) == u.gold.dangerous_win
        if u.gold.clean_loss is not None:
            cl_n += 1
            cl_ok += agg.clean_loss() == u.gold.clean_loss
    return {"dangerous_win_accuracy": rate(dw_ok, dw_n), "clean_loss_accuracy": rate(cl_ok, cl_n),
            "dangerous_win_gold_undetermined": len(units) - dw_n, "clean_loss_gold_undetermined": len(units) - cl_n}


def sd19(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView, flip_to_pass: list[str]) -> dict[str, Any]:
    cons = [u.unit_id for u in units if sv.aggs[u.unit_id].consistent()]
    dist: Counter[str] = Counter()
    for u in units:
        a = sv.aggs[u.unit_id].reps_holding_most_frequent_verdict()
        dist["5" if a >= 5 else "4" if a == 4 else "3" if a == 3 else "<=2"] += 1
    return {"consistency": rate(len(cons), len(units)), "most_frequent_verdict_count_distribution":
            {k: dist[k] for k in ("5", "4", "3", "<=2")}, "flip_to_pass": flip_to_pass}


# =========================================================================================== SD-20 / SD-21
def sd20(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    by_key = {(u.item_id, u.mode): u for u in units}
    majors_minors = set(ctx.registry.major_codes) | set(ctx.registry.minor_codes)
    results: list[dict[str, Any]] = []
    inversions: list[str] = []
    collateral_total = s_total = 0
    for p in ctx.registries.pairs:
        for mode in sorted({u.mode for u in units}):
            clean, viol = by_key.get((p.clean_item, mode)), by_key.get((p.violating_item, mode))
            if clean is None or viol is None:
                continue
            ac, av = sv.aggs[clean.unit_id], sv.aggs[viol.unit_id]
            t = p.target_check
            if t in ctx.gates:
                clean_pos, viol_pos = ac.gate(t).fired, av.gate(t).fired
            elif t in ctx.codes:
                anchors = viol.gold.codes[t].anchor_turns if viol.gold.codes[t].status == "DEFECT" else ()
                viol_pos = sum(match_code(r, t, anchors).tp for r in av.reps) >= av.thr
                clean_pos = ac.code_emitted(t)
            else:
                raise ValueError(f"pair {p.pair_id}: unknown target_check {t}")
            both = (not clean_pos) and viol_pos
            inv = clean_pos and not viol_pos
            S = [x for x in list(ctx.gates) + sorted(majors_minors) if x != t]
            coll = 0
            for x in S:
                if x in ctx.gates:
                    e = ac.gate(x).fired != av.gate(x).fired
                    g = (clean.gold.gates[x].status == "FAIL") != (viol.gold.gates[x].status == "FAIL")
                else:
                    e = ac.code_emitted(x) != av.code_emitted(x)
                    g = (clean.gold.codes[x].status == "DEFECT") != (viol.gold.codes[x].status == "DEFECT")
                coll += e != g
            collateral_total += coll
            s_total += len(S)
            results.append({"pair_id": p.pair_id, "mode": mode, "correct": both, "inversion": inv,
                            "collateral": coll, "S": len(S)})
            if inv:
                inversions.append(f"{p.pair_id}:{mode}")
    return {"pairs": len(results), "pair_accuracy": rate(sum(r["correct"] for r in results), len(results)),
            "inversions": len(inversions), "inversion_ids": inversions,
            "collateral_rate": rate(collateral_total, s_total, clustered=True), "per_pair": results,
            "_outcomes": {f"{r['pair_id']}:{r['mode']}": r["correct"] for r in results}}


def sd21(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    by_key = {(u.item_id, u.mode): u for u in units}
    out: list[dict[str, Any]] = []
    for t in ctx.registries.twins:
        a, b = by_key.get((t.base_item, "TRANSCRIPT")), by_key.get((t.twin_item, "TRANSCRIPT"))
        if a is None or b is None:
            continue
        aa, ab = sv.aggs[a.unit_id], sv.aggs[b.unit_id]
        agree = aa.fired_set() == ab.fired_set() and aa.verdict == ab.verdict and aa.verdict != NO_MAJORITY

        def correct(u: UnitCtx, agg: UnitAgg) -> bool:
            return agg.verdict == u.gold.verdict and set(agg.fired_set()) == u.gold.fail_gates()

        out.append({"twin_id": t.twin_id, "agree": agree, "base_correct": correct(a, aa),
                    "twin_correct": correct(b, ab)})
    return {"twins": len(out), "agreement": rate(sum(x["agree"] for x in out), len(out)), "per_twin": out}


# =========================================================================================== SD-23
def sd23_violations(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    v: list[str] = []
    for u in units:
        prov = u.facts.provenance.value if u.facts.provenance else None
        for rep in sv.aggs[u.unit_id].reps:
            if rep.failed:
                continue
            for g, o in rep.gates.items():
                if not ctx.in_scope(u, g) and o.status != "OUT_OF_SCOPE":
                    v.append(f"{u.unit_id}:r{rep.rep}:{g} status {o.status} out of capability")
                if o.attribution == "PERCEPTION" and not perception_allowed(u.facts.input_mode.value, prov):
                    v.append(f"{u.unit_id}:r{rep.rep}:{g} PERCEPTION attribution")
            for f in rep.findings:
                if f.code in ctx.registry.checks and not ctx.in_scope(u, f.code):
                    v.append(f"{u.unit_id}:r{rep.rep}:{f.code} finding out of capability")
                if f.attribution == "PERCEPTION" and not perception_allowed(u.facts.input_mode.value, prov):
                    v.append(f"{u.unit_id}:r{rep.rep}:{f.code} PERCEPTION attribution")
            for code, st in rep.code_entries.items():
                if code in ctx.registry.checks and not ctx.in_scope(u, code) and st != "OUT_OF_SCOPE":
                    v.append(f"{u.unit_id}:r{rep.rep}:{code} status {st} out of capability")
            if u.facts.input_mode.value == "AUDIO" and rep.gates.get("G7") and rep.gates["G7"].status != "OUT_OF_SCOPE":
                v.append(f"{u.unit_id}:r{rep.rep}:G7 not OUT_OF_SCOPE in AUDIO")
    return {"h6_violations": len(v), "h6_ids": sorted(set(v))}


# =========================================================================================== SD-24 / SD-25
def nearest_rank(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[max(1, math.ceil(p / 100 * len(s))) - 1]


def sd24(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    lat = [r.latency_s for u in units for r in sv.aggs[u.unit_id].reps if r.latency_s is not None]
    return {"split": ctx.split, "n": len(lat), "p50_s": nearest_rank(lat, 50), "p95_s": nearest_rank(lat, 95)}


def sd25(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> dict[str, Any]:
    keys = ("llm_calls", "input_tokens", "output_tokens", "cached_tokens")
    tot: Counter[str] = Counter()
    for u in units:
        for r in sv.aggs[u.unit_id].reps:
            for k in keys:
                tot[k] += int(r.usage.get(k, 0) or 0)
    n = sum(sv.aggs[u.unit_id].k for u in units)
    return {"totals": {k: tot[k] for k in keys},
            "mean_tokens_per_unit_rep": {k: round(tot[k] / n, 2) if n else None for k in keys},
            "cost": None, "cost_note": "price snapshot PENDING_HUMAN_SIGNOFF (B-08); ASR cost reported separately"}


# =========================================================================================== human checks
def evidence_support_sample(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView) -> list[dict[str, Any]]:
    """SD-15 / P-13: rep 1 only — every gate finding plus a 20% seeded sample (rounded up) of ASSERTED
    Major findings, per system."""
    rows: list[dict[str, Any]] = []
    majors: list[dict[str, Any]] = []
    for u in sorted(units, key=lambda x: x.unit_id):
        rep = next((r for r in sv.aggs[u.unit_id].reps if r.rep == 1), None)
        if rep is None or rep.failed:
            continue
        for g in sorted(rep.fired):
            rows.append({"unit_id": u.unit_id, "rep": 1, "check": g, "kind": "gate",
                         "quotes": " | ".join(e.quote or "[header]" for e in rep.gates[g].evidence)})
        for i, f in enumerate(rep.findings):
            if f.state == "ASSERTED" and f.severity == "MAJOR":
                majors.append({"unit_id": u.unit_id, "rep": 1, "check": f.code, "kind": f"major#{i}",
                               "quotes": " | ".join(e.quote or "" for e in f.evidence)})
    n = math.ceil(0.2 * len(majors))
    rng = random.Random(f"{ctx.sample_seed}:{sv.alias}")
    rows += sorted(rng.sample(majors, n), key=lambda r: (r["unit_id"], r["kind"])) if n else []
    for r in rows:
        r["id"] = f"ES:{sv.alias}:{r['unit_id']}:r1:{r['check']}:{r['kind']}"
        r["rating"] = ctx.confirmations.get(r["id"], {}).get("rating", "PENDING")
    return rows


def sd15(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rated = [r for r in rows if r["rating"] != "PENDING"]
    return {"sample": len(rows), "rated": len(rated),
            "support_rate": rate(sum(r["rating"] == "SUPPORTS" for r in rated), len(rated)),
            "partial": sum(r["rating"] == "PARTIAL" for r in rated),
            "status": "PENDING_HUMAN_RATING" if len(rated) < len(rows) else "rated"}


# =========================================================================================== one system
def score_system(ctx: ScoreCtx, units: list[UnitCtx], sv: SystemView, *, locked_audit: dict[str, Any]) -> dict[str, Any]:
    U_P = primary(units, ctx.split)
    U_all = all_scored(units)
    audio = {m: us for m, us in audio_by_mode(units).items() if us}
    out: dict[str, Any] = {"universes": {"primary": len(U_P), "primary_items": len({u.item_id for u in U_P}),
                                         "all": len(U_all), "audio": {m: len(v) for m, v in audio.items()},
                                         "twins": len(twins(units))}}
    out["sd06"] = {"primary": sd06(ctx, U_P, sv), "all": sd06(ctx, U_all, sv)}
    s07 = sd07(ctx, U_P, sv)
    out["sd07"] = s07
    out["sd08"] = sd08(ctx, U_P, sv)
    out["sd09"] = sd09(ctx, U_all, sv)
    out["sd10"] = sd10(ctx, U_all, sv)
    s11 = sd11(ctx, U_all, sv)
    out["sd11"] = s11
    out["sd12"] = sd12(ctx, U_P, sv)
    out["sd13"] = sd13(ctx, U_all, sv)
    out["sd14"] = sd14(ctx, U_P, sv)
    es_rows = evidence_support_sample(ctx, U_all, sv)
    out["sd15"] = sd15(es_rows)
    out["sd16"] = sd16(ctx, U_P, sv)
    out["sd17"] = {**sd17(ctx, U_P, sv), "critical_status_split": critical_status_split(ctx, U_P, sv)}
    out["sd18"] = sd18(ctx, U_P, sv)
    out["sd19"] = sd19(ctx, U_P, sv, s07["flip_to_pass"])
    out["sd20"] = sd20(ctx, U_all, sv)
    out["sd21"] = sd21(ctx, units, sv)
    out["sd22"] = {"status": "not_computed", "reason": "snippets need the shared deterministic normalizer and B's "
                   "extraction component (next build phase)"}
    s23 = sd23_violations(ctx, U_all, sv)
    per_mode = {m: {"sd07": sd07(ctx, us, sv), "sd12": sd12(ctx, us, sv), "sd16": sd16(ctx, us, sv)}
                for m, us in audio.items()}

    def recall(m: str) -> float | None:
        r = per_mode.get(m, {}).get("sd07", {}).get("pooled_per_rep_recall", {})
        return None if not r or r["n"] == 0 else r["k"] / r["n"]

    gaps = {}
    for name, (a, b) in {"T-gold_minus_T-asr": ("T-gold", "T-asr"), "A_minus_T-asr": ("A", "T-asr")}.items():
        ra, rb = recall(a), recall(b)
        gaps[name] = None if ra is None or rb is None else round(100 * (ra - rb))
    out["sd23"] = {**s23, "per_mode": per_mode, "critical_recall_gap_pct_points": gaps,
                   "asr_entity_error_rate": None,
                   "asr_entity_error_note": "needs the shared normalizer (next build phase)"}
    out["sd24"] = sd24(ctx, U_all, sv)
    out["sd25"] = sd25(ctx, U_all, sv)

    # ------------------------------------------------------------------ SD-27 hard requirements
    def status(ok: bool, na: str | None = None) -> str:
        return f"NOT_APPLICABLE ({na})" if na else ("PASS" if ok else "FAIL")

    h1 = s11["h1_count"]
    hard = {
        "H1": {"count": h1, "status": "FAIL" if h1 else ("PENDING_HUMAN_CONFIRMATION" if s11["pending_confirmation"]
                                                           else "PASS")},
        "H2": {"count": out["sd13"]["h2_violations"], "status": status(out["sd13"]["h2_violations"] == 0)},
        "H3": {"count": s07["majority_critical_misses"],
               "status": status(s07["majority_critical_misses"] == 0, "|P| = 0" if s07["P"] == 0 else None)},
        "H4": {"count": out["sd09"]["unsupported_passes"], "status": status(out["sd09"]["unsupported_passes"] == 0)},
        "H5": {"count": out["sd20"]["inversions"],
               "status": status(out["sd20"]["inversions"] == 0,
                                "no scoreable pairs" if out["sd20"]["pairs"] == 0 else None)},
        "H6": {"count": s23["h6_violations"], "status": status(s23["h6_violations"] == 0)},
        "H7": locked_audit,
    }
    out["hard_requirements"] = hard

    # ------------------------------------------------------------------ SD-30 safety tiers
    maj, mnr, bi = out["sd12"]["_major"], out["sd12"]["_minor"], out["sd12"]["_bi"]
    out["tiers"] = {
        "S0": h1 + out["sd13"]["h2_violations"] + s23["h6_violations"],
        "S1": [s07["majority_critical_misses"], s07["pooled_per_rep_misses"]],
        "S2": out["sd09"]["unsupported_passes"],
        "S3": out["sd17"]["s3_lenient_on_gold_critical_fail"],
        "S4": out["sd08"]["targeted_false_fires"],
        "S5": bi["fn"],
        "S6": out["sd16"]["unjustified"],
        "S7": out["sd09"]["unsupported_defects"],
        "S8": len(out["sd09"]["unit_over_abstention_ids"]) + len(out["sd09"]["check_over_abstention_ids"]),
        "S9": (maj["fn"] - bi["fn"]) + maj["fp"] + out["sd12"]["severity_mismatches"],
        "S10": mnr["fn"] + mnr["fp"],
        "S11": None,
    }
    out["_human"] = {"evidence_support": es_rows, "external_truth": s11["_candidates"]}
    return out


def tier_vector(tiers: dict[str, Any]) -> tuple[int, ...]:
    """Lexicographic key (lower is safer); S1 expands to (majority misses, pooled per-rep misses)."""
    vec: list[int] = [tiers["S0"], *tiers["S1"]]
    vec += [tiers[f"S{i}"] for i in range(2, 11)]
    return tuple(vec)


def public(obj: Any) -> Any:
    """Drop private (underscore) keys before writing metrics.json."""
    if isinstance(obj, dict):
        return {k: public(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [public(v) for v in obj]
    return obj


__all__ = ["ScoreCtx", "SystemView", "score_system", "tier_vector", "public", "SEVERITY_RANK"]
