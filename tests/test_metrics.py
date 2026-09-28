"""Scorer test obligations — scoring-spec SD-31 (hand-built fixtures; every expected number is derived by hand
in the comments, never by calling the code under test)."""

from __future__ import annotations

import json

import pytest

import factories as F
from ignosis_eval.contracts.benchmark import ItemMeta, UnitFacts
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.evidence import norm, quote_score
from ignosis_eval.contracts.registries import Registries
from ignosis_eval.golddrv.capability import CapabilityTable
from ignosis_eval.golddrv.derive import derive_mode_gold
from ignosis_eval.metrics.alignment import gate_outcome, observe
from ignosis_eval.metrics.compute import (
    ScoreCtx,
    SystemView,
    nearest_rank,
    score_system,
    sd06,
    sd07,
    sd08,
    sd09,
    sd10,
    sd11,
    sd12,
    sd13,
    sd16,
    sd17,
    sd19,
    sd20,
    sd23_violations,
    tier_vector,
)
from ignosis_eval.metrics.external_truth import COMPILED, structural_violations, text_candidates
from ignosis_eval.metrics.majority import (
    NO_MAJORITY,
    UnitAgg,
    code_majority_status,
    dw_majority,
    gate_majority_status,
    threshold,
    verdict_majority,
)
from ignosis_eval.metrics.matching import faithful, match_code
from ignosis_eval.metrics.slices import UnitCtx

MODE_INPUT = {"TRANSCRIPT": "TRANSCRIPT", "T-gold": "TRANSCRIPT", "T-asr": "TRANSCRIPT", "A": "AUDIO",
              "A+T": "AUDIO_TRANSCRIPT", "A+T-platform": "AUDIO_TRANSCRIPT"}


# ------------------------------------------------------------------------------------------ helpers
def unit(gold, *, mode="TRANSCRIPT", pack="micro", split="holdout", header=False, ts=False, prov=None) -> UnitCtx:
    arts = {"transcript": "t.txt"} if mode in ("TRANSCRIPT", "T-gold", "A+T") else {}
    if mode != "TRANSCRIPT":
        arts["audio"] = "a.wav"
    if mode == "A+T-platform":
        arts["platform_transcript"] = "p.txt"
    meta = ItemMeta(item_id=gold.item_id, split=split, pack=pack, language="en", unit_modes=[mode], artifacts=arts)
    facts = UnitFacts(item_id=gold.item_id, unit_mode=mode, input_mode=MODE_INPUT[mode], has_call_start_ts=header,
                      has_timestamps=ts, provenance=prov, truncated_start=False)
    return UnitCtx(facts.unit_id, meta, facts, derive_mode_gold(gold, facts, F.spec().rubric))


def g(item="ZZ-M01", split="holdout", **kw):
    return F.gold(item, split=split, **kw)


def agg(records, gates=F.GATES) -> UnitAgg:
    return UnitAgg([observe(r, i) for i, r in enumerate(records, 1)], tuple(gates))


def ctx(registries: Registries | None = None, split: str = "holdout") -> ScoreCtx:
    sp = F.spec()
    return ScoreCtx(rubric=sp.rubric, registry=sp.registry, table=CapabilityTable.from_rubric(sp.rubric),
                    quote_match_min=90.0, registries=registries or Registries(), split=split, sample_seed=1)


def view(**by_unit) -> SystemView:
    return SystemView("SYS-1", {k: v for k, v in by_unit.items()}, {})


def recs(*specs):
    """specs: 'P' pass record, 'F' G3 FAIL SUSPECTED, 'C' G3 FAIL CONFIRMED, 'X' EVALUATION_FAILED."""
    out = []
    for s in specs:
        if s == "X":
            out.append(F.failed())
        elif s == "P":
            out.append(F.record())
        else:
            out.append(F.record(gates={"G3": F.gate("G3", "FAIL", critical_status="CONFIRMED" if s == "C" else
                                                     "SUSPECTED", evidence=[F.ev(3, "stub agent line gamma")])}))
    return out


# ================================================================================ SD-31.1 every SD-06 cell
SPEC_TABLE = {  # typed from scoring-spec.md SD-06
    ("FAIL", None): ["hit", "hit_soft", "critical_miss", "soft_miss", "scope_error", "miss"],
    ("PASS", None): ["critical_fp", "fp_soft", "correct", "over_abstention", "scope_error", "correct"],
    ("INCONCLUSIVE", True): ["overclaim", "correct", "unsupported_pass", "partial", "scope_error", "unsupported_pass"],
    ("INCONCLUSIVE", False): ["overclaim", "tolerated", "unsupported_pass", "correct", "tolerated", "unsupported_pass"],
    ("OUT_OF_SCOPE", None): ["capability_violation"] * 3 + ["tolerated", "correct", "tolerated"],
    ("NA", None): ["fp", "fp_soft", "correct", "tolerated", "tolerated", "correct"],
}
COLUMNS = [("FAIL", "CONFIRMED"), ("FAIL", "SUSPECTED"), ("PASS", None), ("INCONCLUSIVE", None),
           ("OUT_OF_SCOPE", None), ("NA", None)]


@pytest.mark.parametrize("row", list(SPEC_TABLE))
@pytest.mark.parametrize("ci", range(6))
def test_sd06_every_cell(row, ci):
    status, crit = COLUMNS[ci]
    assert gate_outcome(row[0], row[1], status, crit) == SPEC_TABLE[row][ci]


def test_sd06_evaluation_failed_is_not_fired():
    assert gate_outcome("FAIL", None, None, None) == "evaluation_failed"


# ================================================================================ SD-31.2 majority (AJ-11)
def _gate_recs(statuses, gate="G1"):
    out = []
    for s in statuses:
        out.append(F.failed() if s == "X" else F.record(gates={gate: F.gate(gate, s)}))
    return [observe(r, i) for i, r in enumerate(out, 1)]


@pytest.mark.parametrize("statuses", [
    ["FAIL", "FAIL", "PASS", "PASS", "NA"],                   # formerly FAIL by precedence: now no label reaches 3
    ["INCONCLUSIVE", "INCONCLUSIVE", "PASS", "PASS", "NA"],
    ["NA", "NA", "PASS", "PASS", "FAIL"],
    ["OUT_OF_SCOPE", "OUT_OF_SCOPE", "NA", "NA", "PASS"],
    ["FAIL", "PASS", "INCONCLUSIVE", "X", "X"],               # EF is never INCONCLUSIVE (no tie-breaking role)
    ["PASS", "PASS", "X", "X", "X"],                          # EF contributes false to every indicator
    ["X"] * 5,
])
def test_sd04_gate_2_2_1_is_no_majority(statuses):
    assert gate_majority_status(_gate_recs(statuses), "G1") == NO_MAJORITY


@pytest.mark.parametrize("statuses,expected", [
    (["FAIL", "FAIL", "FAIL", "PASS", "PASS"], "FAIL"),
    (["PASS", "PASS", "PASS", "X", "X"], "PASS"),
    (["NA", "NA", "NA", "FAIL", "FAIL"], "NA"),
    (["INCONCLUSIVE", "INCONCLUSIVE", "INCONCLUSIVE", "PASS", "PASS"], "INCONCLUSIVE"),
    (["OUT_OF_SCOPE", "OUT_OF_SCOPE", "OUT_OF_SCOPE", "NA", "X"], "OUT_OF_SCOPE"),
])
def test_sd04_gate_three_of_five(statuses, expected):
    assert gate_majority_status(_gate_recs(statuses), "G1") == expected


def test_sd04_status_set_indicator():
    # PASS x2 + NA x1: no single status reaches 3, but the {PASS, NA} indicator holds in 3 reps (SD-09 H4 input)
    a = UnitAgg(_gate_recs(["PASS", "PASS", "NA", "INCONCLUSIVE", "X"]), F.GATES)
    assert a.gate("G1").status == NO_MAJORITY
    assert a.gate_in("G1", ("PASS", "NA")) and not a.gate_in("G1", ("INCONCLUSIVE", "OUT_OF_SCOPE"))


def test_sd04_threshold_is_three_of_five():
    assert (threshold(5), threshold(3), threshold(1)) == (3, 2, 1)


def test_sd04_worked_example_2_fail_2_pass_1_ef():
    """AJ-11 fixture: 2 FAIL + 2 PASS + 1 EVALUATION_FAILED.

    fired = 2 < 3 -> not detected (H3 fails for a gold-FAIL unit); passed = 2 < 3 -> not an H4 unsupported pass for
    a gold-INCONCLUSIVE unit; gate majority status NO_MAJORITY (SD-06 majority column NO_MAJORITY, never correct).
    """
    a = UnitAgg(_gate_recs(["FAIL", "FAIL", "PASS", "PASS", "X"], gate="G3"), F.GATES)
    m = a.gate("G3")
    assert (m.status, m.fired, m.detected, m.critical_status) == (NO_MAJORITY, False, 2, None)
    assert "G3" not in a.fired_set()
    reps = ["F", "F", "P", "P", "X"]
    fail_u = unit(g("ZZ-W01", gates={"G3": {"status": "FAIL", "anchor_turns": [3]}}))
    inc_u = unit(g("ZZ-W02", gates={"G3": {"status": "INCONCLUSIVE", "trigger": True}}, wsc=False))
    sv = view(**{fail_u.unit_id: agg(recs(*reps)), inc_u.unit_id: agg(recs(*reps))})
    s7 = sd07(ctx(), [fail_u], sv)
    assert (s7["detected_counts"][f"{fail_u.unit_id}:G3"], s7["majority_critical_misses"]) == (2, 1)  # H3 count 1
    s9 = sd09(ctx(), [inc_u], sv)
    assert f"{inc_u.unit_id}:G3" not in s9["unsupported_pass_ids"]  # PASS in 2 reps only
    s6 = sd06(ctx(), [fail_u, inc_u], sv)
    assert s6["majority"]["FAIL"]["NO_MAJORITY"] == 1 and s6["majority"]["INCONCLUSIVE+trigger"]["NO_MAJORITY"] == 1
    assert s6["majority"]["FAIL"]["FAIL+SUSPECTED"] == 0 and s6["majority"]["FAIL"]["PASS"] == 0


def test_sd04_worked_example_h3_status(spec):
    ni = F.make_ni(spec)
    u = unit(g("ZZ-W03", gates={"G3": {"status": "FAIL", "anchor_turns": [3]}}), header=True)
    sv = SystemView("SYS-1", {u.unit_id: agg(recs("F", "F", "P", "P", "X"))}, {(u.unit_id, r): ni for r in range(1, 6)})
    out = score_system(ctx(), [u], sv, locked_audit={"status": "NOT_APPLICABLE (dev run)"})
    assert out["hard_requirements"]["H3"]["count"] == 1 and out["hard_requirements"]["H3"]["status"] == "FAIL"
    assert out["hard_requirements"]["H4"]["count"] == 0


def test_sd04_codes():
    def r(kind):
        if kind == "D":
            return F.record(findings=[F.finding("UND-01", [2])])
        if kind == "N":
            return F.record(checks=[{"code": "UND-01", "status": "NA"}])
        if kind == "X":
            return F.failed()
        return F.record()
    reps = [observe(r(k), i) for i, k in enumerate("DDPPN", 1)]
    assert code_majority_status(reps, "UND-01") == NO_MAJORITY
    assert UnitAgg(reps, F.GATES).code_emitted("UND-01") is False  # emitted in 2 reps only
    reps = [observe(r(k), i) for i, k in enumerate("DDDPN", 1)]
    assert code_majority_status(reps, "UND-01") == "DEFECT"
    reps = [observe(r(k), i) for i, k in enumerate("PPXXX", 1)]
    assert code_majority_status(reps, "UND-01") == NO_MAJORITY  # EF is not a pass


def _verdict_recs(verdicts):
    out = []
    for v in verdicts:
        if v == "X":
            out.append(F.failed())
        elif v == "CRITICAL_FAIL":
            out.append(F.record(gates={"G3": "FAIL"}))
        else:
            out.append(F.record(verdict=v))
    return [observe(r, i) for i, r in enumerate(out, 1)]


@pytest.mark.parametrize("verdicts,expected", [
    (["CRITICAL_FAIL", "CRITICAL_FAIL", "MEETS_BAR", "MEETS_BAR", "NEEDS_ATTENTION"], NO_MAJORITY),
    (["MEETS_BAR", "MEETS_BAR", "NEEDS_ATTENTION", "NEEDS_ATTENTION", "X"], NO_MAJORITY),
    (["NOT_EVALUABLE", "NOT_EVALUABLE", "X", "X", "MEETS_BAR"], NO_MAJORITY),
    (["CRITICAL_FAIL", "CRITICAL_FAIL", "CRITICAL_FAIL", "MEETS_BAR", "MEETS_BAR"], "CRITICAL_FAIL"),
    (["X", "X", "X", "MEETS_BAR", "MEETS_BAR"], "EVALUATION_FAILED"),  # EF is a value for the verdict only
])
def test_sd04_verdicts(verdicts, expected):
    assert verdict_majority(_verdict_recs(verdicts)) == expected


def test_sd04_no_majority_is_never_correct():
    u = unit(g("ZZ-W04"))  # gold MEETS_BAR
    reps = _verdict_recs(["MEETS_BAR", "MEETS_BAR", "NEEDS_ATTENTION", "NEEDS_ATTENTION", "X"])
    s = sd17(ctx(), [u], view(**{u.unit_id: UnitAgg(reps, F.GATES)}))
    assert (s["accuracy"]["kn"], s["no_majority"], s["errors"][u.unit_id]) == ("0/1", 1, "no_majority")
    assert s["accuracy_pooled_per_rep"]["kn"] == "2/5"  # per-rep accuracy is unaffected


def test_sd04_dangerous_win_majority():
    def dw(v):
        return F.record(tags={"dangerous_win": v}) if v != "X" else F.failed()
    reps = [observe(dw(v), i) for i, v in enumerate(["NONE", "NONE", "MATERIAL", "MATERIAL", "X"], 1)]
    assert dw_majority(reps) == NO_MAJORITY
    reps = [observe(dw(v), i) for i, v in enumerate(["NONE", "NONE", "NONE", "MATERIAL", "X"], 1)]
    assert dw_majority(reps) == "NONE"


def test_no_precedence_constants_remain():
    import ignosis_eval.metrics.alignment as al
    import ignosis_eval.metrics.majority as mj
    for mod in (al, mj):
        assert not [n for n in dir(mod) if "PRECEDENCE" in n.upper()]


# ================================================================================ AJ-12 critical-status scoring
def test_critical_status_mismatch_metric_removed(spec):
    ni = F.make_ni(spec)
    u = unit(g("ZZ-W05", gates={"G3": {"status": "FAIL", "anchor_turns": [3]}}), header=True)
    sv = SystemView("SYS-1", {u.unit_id: agg(recs("C", "C", "C", "F", "F"))}, {(u.unit_id, r): ni for r in range(1, 6)})
    out = score_system(ctx(), [u], sv, locked_audit={"status": "NOT_APPLICABLE (dev run)"})
    dumped = json.dumps(out).lower()
    assert "critical_status_mismatch" not in dumped and "critical_mismatch" not in dumped
    assert "severity_mismatches" in out["sd12"]  # SD-12 severity agreement is a different metric and stays
    split = out["sd17"]["critical_status_split"]
    assert split["per_rep"] == {"CONFIRMED": 3, "SUSPECTED": 2} and split["majority"] == {"CONFIRMED": 1,
                                                                                          "SUSPECTED": 0}
    assert "overclaims" in out["sd09"]  # overclaim is kept (SD-09)


def test_overclaim_still_scored():
    u = unit(g("ZZ-W06", gates={"G3": {"status": "INCONCLUSIVE", "trigger": True}}, wsc=False))
    s = sd09(ctx(), [u], view(**{u.unit_id: agg(recs("C", "C", "C", "P", "P"))}))
    assert s["overclaims"] == 1 and s["overclaim_ids"] == [f"{u.unit_id}:G3"]


# ================================================================================ SD-31.3 anchors ±1, wrong anchor
@pytest.mark.parametrize("turn,tp,fp,fn", [(5, True, False, False), (6, True, False, False), (4, True, False, False),
                                          (7, False, True, True), (3, False, True, True)])
def test_sd12_anchor_tolerance(turn, tp, fp, fn):
    rep = observe(F.record(findings=[F.finding("UND-01", [turn])]), 1)
    m = match_code(rep, "UND-01", (5,))
    assert (m.tp, m.fp, m.fn) == (tp, fp, fn)


def test_sd12_possible_excluded_and_duplicates_collapse():
    rep = observe(F.record(findings=[F.finding("UND-01", [9], state="POSSIBLE"),
                                     F.finding("UND-01", [5]), F.finding("UND-01", [5])]), 1)
    m = match_code(rep, "UND-01", (5,))
    assert (m.tp, m.fp, m.fn, m.evaluator_turns) == (True, False, False, (5,))
    only_possible = observe(F.record(findings=[F.finding("UND-01", [5], state="POSSIBLE")]), 1)
    assert match_code(only_possible, "UND-01", (5,)).fn and only_possible.code_status("UND-01") == "INCONCLUSIVE"


# ================================================================================ SD-31.4 EVALUATION_FAILED
def test_evaluation_failed_handling():
    u = unit(g(gates={"G3": {"status": "FAIL", "anchor_turns": [3]}},
               findings=[{"code": "UND-01", "anchor_turns": [2], "severity": "MAJOR"}], verdict="CRITICAL_FAIL"))
    a = agg(recs("C", "C", "C", "C", "X"))
    s = sd07(ctx(), [u], view(**{u.unit_id: a}))
    assert s["detected_counts"][f"{u.unit_id}:G3"] == 4  # SD-05: EF not detected
    s12 = sd12(ctx(), [u], view(**{u.unit_id: a}))
    assert s12["per_code"]["UND-01"]["fn"] == 5  # SD-12: EF rep -> FN (the other reps never cite UND-01)
    s19 = sd19(ctx(), [u], view(**{u.unit_id: a}), [])
    assert s19["consistency"]["k"] == 0  # SD-19: EF counts as a verdict value


# ================================================================================ SD-31.5 SD-13 algorithm
def test_sd13_normalization_and_score():
    assert norm("₹5,000!  Rs.") == "5 000 rs"  # ₹ (Sc) and punctuation -> space; casefold; collapse
    assert quote_score("₹5,000", "please pay ₹ 5,000 today") == 100.0
    assert quote_score("stub, agent line", "stub agent line alpha") == 100.0
    assert quote_score("abcdef", "abc") == 50.0  # |Q| > |T|: 100 * (1 - 3/6)
    assert quote_score("", "abc") == 0.0
    assert quote_score("abcx", "zzabcyzz") == 75.0  # best window "abcy": lev 1 / 4


def test_sd13_faithful_rules(spec):
    ni: NormalizedInput = F.make_ni(spec)
    assert faithful(F.ev(3, "stub agent line gamma"), ni, 90)[0]
    assert faithful(F.ev(3, "stub agent line gamma", "BORROWER"), ni, 90) == (False, "role mismatch")
    assert faithful(F.ev(9, "stub"), ni, 90) == (False, "turn does not exist")
    assert not faithful(F.ev(3, "something the agent never said"), ni, 90)[0]


def test_sd13_h2_counts_only_unflagged_gate_quotes(spec):
    ni = F.make_ni(spec)
    u = unit(g())
    bad = F.gate("G3", "FAIL", evidence=[F.ev(3, "fabricated quote text")])
    flagged = F.gate("G3", "FAIL", evidence=[F.ev(3, "fabricated quote text")], evidence_unverified=True)
    a = agg([F.record(gates={"G3": bad})] + [F.record(gates={"G3": flagged})] * 4)
    sv = SystemView("SYS-1", {u.unit_id: a}, {(u.unit_id, r): ni for r in range(1, 6)})
    s = sd13(ctx(), [u], sv)
    assert (s["h2_violations"], s["flagged_unfaithful_gate_quotes"], s["faithfulness"]["kn"]) == (1, 4, "0/5")


# ================================================================================ SD-31.6 inversion, collateral
def test_sd20_inversion_and_collateral():
    reg = Registries.model_validate({"pairs": [{"pair_id": "ZZ-P1", "clean_item": "ZZ-C01",
                                                "violating_item": "ZZ-V01", "target_check": "G3"}]})
    clean = unit(g("ZZ-C01"))
    viol = unit(g("ZZ-V01", gates={"G3": {"status": "FAIL", "anchor_turns": [3]}}))
    fire_g3_and_g1 = F.record(gates={"G3": F.gate("G3", "FAIL", evidence=[F.ev(3, "stub agent line gamma")]),
                                     "G1": F.gate("G1", "FAIL", evidence=[F.ev(1, "stub agent line alpha")])})
    sv = view(**{clean.unit_id: agg([fire_g3_and_g1] * 5), viol.unit_id: agg(recs("P", "P", "P", "P", "P"))})
    s = sd20(ctx(reg), [clean, viol], sv)
    assert s["inversions"] == 1 and s["pair_accuracy"]["kn"] == "0/1"
    # S = 8 other gates + 27 Major/Minor codes (COM-03 counted once); G1 differs in evaluator, not in gold -> 1
    assert s["per_pair"][0]["collateral"] == 1
    n_s = 8 + len(set(F.spec().registry.major_codes) | set(F.spec().registry.minor_codes))
    assert s["per_pair"][0]["S"] == n_s


def test_sd20_code_target_uses_match():
    reg = Registries.model_validate({"pairs": [{"pair_id": "ZZ-P2", "clean_item": "ZZ-C02",
                                                "violating_item": "ZZ-V02", "target_check": "UND-01"}]})
    clean = unit(g("ZZ-C02"))
    viol = unit(g("ZZ-V02", findings=[{"code": "UND-01", "anchor_turns": [4], "severity": "MAJOR"}],
                  verdict="NEEDS_ATTENTION"))
    wrong_anchor = F.record(findings=[F.finding("UND-01", [1])])
    right = F.record(findings=[F.finding("UND-01", [4])])
    sv = view(**{clean.unit_id: agg([F.record()] * 5), viol.unit_id: agg([right] * 2 + [wrong_anchor] * 3)})
    s = sd20(ctx(reg), [clean, viol], sv)
    assert s["pair_accuracy"]["kn"] == "0/1" and s["inversions"] == 0  # matched in 2 reps only; clean not emitted


# ================================================================================ SD-31.7 SD-11 regexes
MUST_HIT = [
    (1, "the payment has been received"), (1, "Payment successful"), (2, "the EMI is correct"),
    (2, "outstanding was wrong"), (3, "CRM was updated"), (3, "records incorrect"),
    (4, "callback has been scheduled"), (5, "waiver was approved"), (5, "offer within authority"),
    (5, "settlement authorised"), (6, "PTP was honoured"), (6, "ptp broken"), (7, "customer is verified"),
    (7, "borrower was the actual borrower"),
]
MUST_NOT_HIT = ["the borrower says the payment will be made tomorrow", "agent stated an amount",
                "callback requested by the borrower", "waiver requested", "ptp date stated",
                "customer is angry", "the system prompt", "balance enquiry", "records show nothing about this"]


@pytest.mark.parametrize("pattern,text", MUST_HIT)
def test_sd11_must_hit(pattern, text):
    assert COMPILED[pattern - 1].search(text)


@pytest.mark.parametrize("text", MUST_NOT_HIT)
def test_sd11_must_not_hit(text):
    assert not any(rx.search(text) for rx in COMPILED)


def test_sd11_fields_structural_and_quote_exclusion(spec):
    oos = frozenset(spec.registry.oos_codes)
    r = F.record(findings=[F.finding("UND-01", [2], quote="payment has been received",
                                     description="Agent said payment has been received")])
    c = text_candidates(r)
    assert [x.field for x in c] == ["findings[0].description"]  # evidence quote excluded
    assert structural_violations(F.record(findings=[F.finding("ACC-01", [1])]), oos_codes=oos,
                                 has_call_start_ts=True) == ["(a) finding on always-OUT_OF_SCOPE code ACC-01"]
    assert structural_violations(F.record(outcome={"verified": {"payment": True}}), oos_codes=oos,
                                 has_call_start_ts=True) == ["(b) non-null field under outcome.verified"]
    assert structural_violations(F.record(outcome={"verified": {"payment": None}}), oos_codes=oos,
                                 has_call_start_ts=True) == []
    assert structural_violations(F.record(), oos_codes=oos, has_call_start_ts=False) == \
        ["(c) G7 status PASS without header call_start_ts"]
    u = unit(g())
    s = sd11(ctx(), [u], view(**{u.unit_id: agg([r] + [F.record(gates={"G7": "OUT_OF_SCOPE"})] * 4)}))
    # rep 1: G7 PASS without header -> structural (c); description -> 1 textual candidate (pending)
    assert (s["structural_violations"], s["textual_candidates"], s["h1_count"]) == (1, 1, 1)


# ================================================================================ SD-07/08/09/10/16/17/23/24
def test_sd07_numbers():
    u1 = unit(g("ZZ-A01", gates={"G3": {"status": "FAIL", "anchor_turns": [3]}}))
    u2 = unit(g("ZZ-A02", gates={"G3": {"status": "FAIL", "anchor_turns": [3]}}))
    u3 = unit(g("ZZ-A03", gates={"G3": {"status": "FAIL", "contested": True}}))  # contested: not in P
    sv = view(**{u1.unit_id: agg(recs("F", "F", "F", "F", "F")), u2.unit_id: agg(recs("F", "F", "P", "P", "X")),
                 u3.unit_id: agg(recs("P", "P", "P", "P", "P"))})
    s = sd07(ctx(), [u1, u2, u3], sv)
    # P = 2; detections 5 and 2 -> pooled 7/10; stable 1/2; S1 = 1 (2 <= 2); flip-to-pass = u2
    assert (s["P"], s["pooled_per_rep_recall"]["kn"], s["stability"]["kn"], s["majority_critical_misses"]) == \
        (2, "7/10", "1/2", 1)
    assert s["flip_to_pass"] == [f"{u2.unit_id}:G3"] and s["pooled_per_rep_misses"] == 3


def test_sd08_controls():
    reg = Registries.model_validate({"controls": [{"item_id": "ZZ-K01", "target_gates": ["G3"]}]})
    u = unit(g("ZZ-K01"))
    s = sd08(ctx(reg), [u], view(**{u.unit_id: agg(recs("F", "F", "F", "P", "P"))}))
    assert (s["C"], s["targeted_false_fires"], s["confirmed_only_targeted_false_fires"]) == (1, 1, 0)
    # global universe: 8 gates with gold PASS/NA (G7 derives OUT_OF_SCOPE: no header) -> 1/8
    assert s["suspected_only_targeted_fires"]["kn"] == "1/1" and s["global_false_fires"]["kn"] == "1/8"


def test_sd09_unsupported_pass_overclaim_over_abstention():
    u1 = unit(g("ZZ-B01", gates={"G1": {"status": "INCONCLUSIVE", "trigger": False}}, wsc=False))
    u2 = unit(g("ZZ-B02", gates={"G3": {"status": "INCONCLUSIVE", "trigger": True}}, wsc=False))
    ne = F.record(verdict="NOT_EVALUABLE")
    u3 = unit(g("ZZ-B03"))
    sv = view(**{u1.unit_id: agg(recs("P", "P", "P", "P", "P")), u2.unit_id: agg(recs("C", "C", "C", "P", "P")),
                 u3.unit_id: agg([ne] * 5)})
    s = sd09(ctx(), [u1, u2, u3], sv)
    # u1 G1 gold INC, majority PASS -> unsupported pass; u1/u3 G7 gold OOS (no header) vs PASS -> unsupported pass
    assert f"{u1.unit_id}:G1" in s["unsupported_pass_ids"] and s["overclaims"] == 1
    assert s["unit_over_abstention"]["kn"] == "1/3"


def test_sd10_abstention():
    u = unit(g("ZZ-D01", gates={"G1": {"status": "INCONCLUSIVE", "trigger": True}}, wsc=False,
               abstention_targets=[{"check": "G1", "expected": "SUSPECTED"},
                                   {"check": "VERDICT", "expected": "NOT_EVALUABLE"}]))
    sus = F.record(gates={"G1": F.gate("G1", "FAIL", critical_status="SUSPECTED")})
    s = sd10(ctx(), [u], view(**{u.unit_id: agg([sus] * 3 + [F.record()] * 2)}))
    assert s["abstention_recall"]["kn"] == "1/2"  # SUSPECTED in 3 reps; verdict not NOT_EVALUABLE


def test_sd16_unjustified_attribution():
    u = unit(g("ZZ-E01", findings=[{"code": "UND-01", "anchor_turns": [2], "severity": "MAJOR"}],
               verdict="NEEDS_ATTENTION"))
    assert u.gold.codes["UND-01"].attribution == "INDETERMINATE"  # non_response, T-mode, not registered
    good = F.record(findings=[F.finding("UND-01", [2], attribution="INDETERMINATE")])
    bad = F.record(findings=[F.finding("UND-01", [2], attribution="AGENT_BEHAVIOR")])
    s = sd16(ctx(), [u], view(**{u.unit_id: agg([good] * 3 + [bad] * 2)}))
    assert (s["accuracy"]["kn"], s["unjustified"], s["unjustified_rate"]["kn"]) == ("3/5", 2, "2/5")


def test_sd17_errors():
    cf = unit(g("ZZ-H01", gates={"G3": {"status": "FAIL", "anchor_turns": [3]}}))
    na = unit(g("ZZ-H02", findings=[{"code": "UND-01", "anchor_turns": [2], "severity": "MAJOR"}],
                verdict="NEEDS_ATTENTION"))
    mb = unit(g("ZZ-H03"))
    sv = view(**{cf.unit_id: agg(recs("P", "P", "P", "P", "P")), na.unit_id: agg([F.record(gates={"G2": "FAIL"})] * 5),
                 mb.unit_id: agg(recs("X", "X", "X", "P", "P"))})
    s = sd17(ctx(), [cf, na, mb], sv)
    assert s["errors"] == {cf.unit_id: "lenient", na.unit_id: "strict", mb.unit_id: "failed"}
    assert s["s3_lenient_on_gold_critical_fail"] == 1 and s["accuracy"]["kn"] == "0/3"
    assert s["accuracy_pooled_per_rep"]["kn"] == "2/15"


def test_sd23_capability_violations():
    t = unit(g("ZZ-I01"))
    a = unit(g("ZZ-I02"), mode="A", ts=True)
    t_rec = F.record(gates={"G7": "OUT_OF_SCOPE"}, findings=[F.finding("PLT-02", [1], severity="MINOR"),
                                                              F.finding("UND-03", [2], attribution="PERCEPTION")])
    a_rec = F.record(unit_mode="A", gates={"G7": "PASS"})
    s = sd23_violations(ctx(), [t, a], view(**{t.unit_id: agg([t_rec] * 5), a.unit_id: agg([a_rec] * 5)}))
    kinds = " ".join(s["h6_ids"])
    assert "PLT-02 finding out of capability" in kinds and "PERCEPTION" in kinds and "G7 not OUT_OF_SCOPE" in kinds


def test_sd24_nearest_rank():
    vals = [float(v) for v in range(1, 11)]
    assert (nearest_rank(vals, 50), nearest_rank(vals, 95)) == (5.0, 10.0)
    assert nearest_rank([], 50) is None


# ================================================================================ SD-31.8 hand-scored fixture
def test_ten_fixture_records_rescored_by_hand(spec):
    """2 units x 5 reps = 10 records. Expected values computed by hand:

    unit X (gold: G3 FAIL anchor 3; UND-01 DEFECT anchor 2, MAJOR; verdict CRITICAL_FAIL)
      reps: C+UND01@2, C+UND01@2, F+UND01@6, F, EF
      G3 detected = 4 (C,C,F,F) -> majority fired, CONFIRMED in 2 reps -> SUSPECTED majority
      UND-01: TP r1,r2; r3 E={6} vs A={2}: FP+FN; r4 FN; r5 FN -> TP 2, FP 1, FN 3
      verdicts CF,CF,CF,CF,EF -> CRITICAL_FAIL (correct); consistent? no
    unit Y (gold: all PASS, MEETS_BAR; control on G3)
      reps: P, P, P, F, P -> G3 detected 1 -> no targeted false fire; verdict MEETS_BAR x4, CF x1 -> MEETS_BAR
    Pooled recall on P = {X:G3}: 4/5; stability 0/1; S1 0; UND-01 precision 2/3, recall 2/5
    Verdict accuracy 2/2; consistency 0/2; tiers S1 = [0, 1]
    """
    reg = Registries.model_validate({"controls": [{"item_id": "ZZ-Y01", "target_gates": ["G3"]}]})
    ux = unit(g("ZZ-X01", gates={"G3": {"status": "FAIL", "anchor_turns": [3]}},
                findings=[{"code": "UND-01", "anchor_turns": [2], "severity": "MAJOR"}]), header=True)
    uy = unit(g("ZZ-Y01"), header=True)
    g3c = F.gate("G3", "FAIL", critical_status="CONFIRMED", evidence=[F.ev(3, "stub agent line gamma")])
    g3s = F.gate("G3", "FAIL", critical_status="SUSPECTED", evidence=[F.ev(3, "stub agent line gamma")])
    und = lambda t: F.finding("UND-01", [t], quote="stub borrower line beta" if t == 2 else "stub agent line gamma",  # noqa: E731
                              role="BORROWER" if t == 2 else "AGENT")
    x = [F.record(gates={"G3": g3c}, findings=[und(2)]), F.record(gates={"G3": g3c}, findings=[und(2)]),
         F.record(gates={"G3": g3s}, findings=[und(6)]), F.record(gates={"G3": g3s}), F.failed()]
    y = [F.record(), F.record(), F.record(), F.record(gates={"G3": g3s}), F.record()]
    ni = F.make_ni(spec, F.STUB_TURNS + (("AGENT", "stub agent line zeta"),))
    sv = SystemView("SYS-1", {ux.unit_id: agg(x), uy.unit_id: agg(y)},
                    {(u, r): ni for u in (ux.unit_id, uy.unit_id) for r in range(1, 6)})
    out = score_system(ctx(reg), [ux, uy], sv, locked_audit={"status": "NOT_APPLICABLE (dev run)"})
    assert out["sd07"]["pooled_per_rep_recall"]["kn"] == "4/5" and out["sd07"]["stability"]["kn"] == "0/1"
    assert out["sd07"]["majority_critical_misses"] == 0
    assert out["sd08"]["targeted_false_fires"] == 0
    u01 = out["sd12"]["per_code"]["UND-01"]
    assert (u01["tp"], u01["fp"], u01["fn"]) == (2, 1, 3)
    assert u01["precision"]["kn"] == "2/3" and u01["recall"]["kn"] == "2/5"
    assert out["sd17"]["accuracy"]["kn"] == "2/2" and out["sd19"]["consistency"]["kn"] == "0/2"
    assert out["tiers"]["S1"] == [0, 1] and out["hard_requirements"]["H3"]["status"] == "PASS"
    assert tier_vector(out["tiers"])[:3] == (out["tiers"]["S0"], 0, 1)
