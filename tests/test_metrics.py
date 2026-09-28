"""Scorer / metric definitions tested with HANDCRAFTED evaluator outputs (no evaluator involved)."""

from __future__ import annotations

import pytest

from factories import (
    PROFILE,
    ev,
    expected_defect,
    finding,
    gate,
    make_case,
    make_gold,
    make_input,
    make_record,
)
from ignosis_eval.contracts.evaluation_record import AttributionClaim
from ignosis_eval.contracts.enums import (
    AttributionTarget,
    EvidenceModality,
    GateStatus,
    InputMode,
    Severity,
    Verdict,
)
from ignosis_eval.metrics import definitions as M
from ignosis_eval.metrics.alignment import RawOutput, align
from ignosis_eval.metrics.slices import language_delta
from ignosis_eval.scoring.scorer import ScoringConfig, discordances, score_item_reps
from ignosis_eval.stats.proportion import IntervalPolicy, Proportion

POL = IntervalPolicy(min_n=1)  # permissive for unit tests; interval policy itself is tested separately
THREAT = "DEF_THREAT_OR_INTIMIDATION"
G_THREAT = "G_NO_THREATS_OR_ABUSE"


def IR(item, gold, record, rep=1, inp=None, case=None, raw_record=None):
    inp = inp or make_input(call_id="call-0001")
    case = case or make_case(f"case-{item}")
    raw = RawOutput(record_json=raw_record if raw_record is not None else (record.to_json_dict() if record else None),
                    normalized_input_json=inp.to_json_dict())
    return align(item, rep, case, gold, raw, PROFILE)


def threat_gold(item="c-crit", **kw):
    return make_gold(item, verdict=Verdict.FAIL, gates=[(G_THREAT, GateStatus.FAIL)],
                     defects=[expected_defect(THREAT, gate_id=G_THREAT, required_turns=["t03"])], **kw)


def threat_record(**kw):
    return make_record(verdict=Verdict.FAIL, gates=[gate(G_THREAT, GateStatus.FAIL, ["e1"])],
                       findings=[finding("f1", THREAT, gate_id=G_THREAT)],
                       evidence=[ev("e1", ["t03"], "overdue")], **kw)


# ------------------------------------------------------------------------------------------ safety
def test_critical_miss_detection():
    g = threat_gold()
    irs = [
        IR("a", g, threat_record()),                                                      # detected (finding)
        IR("b", g, make_record(verdict=Verdict.FAIL, gates=[gate(G_THREAT, GateStatus.FAIL)])),  # gate only
        IR("c", g, make_record(verdict=Verdict.PASS)),                                    # miss
        IR("d", g, make_record(verdict=Verdict.INCONCLUSIVE)),                            # miss (abstained)
        IR("e", g, None),                                                                 # miss (no record)
    ]
    r = M.critical_misses(irs, POL)
    assert (r.value.k, r.value.n) == (3, 5)
    assert r.breakdown["misses_by_abstention"] == 1 and r.breakdown["misses_by_missing_record"] == 1
    assert r.direction == "lower_is_better"


def test_critical_positive_stability_requires_all_reps():
    g = threat_gold()
    irs = [IR("s", g, threat_record(), rep=1), IR("s", g, threat_record(), rep=2),
           IR("u", g, threat_record(), rep=1), IR("u", g, make_record(), rep=2),
           IR("n", g, make_record(), rep=1), IR("n", g, make_record(), rep=2)]
    r = M.critical_positive_stability(irs, POL)
    assert (r.value.k, r.value.n) == (1, 3)
    assert r.breakdown["unstable_items"] == ["u"] and r.breakdown["never_detected_items"] == ["n"]
    single = M.critical_positive_stability([irs[0]], POL)
    assert single.status == "not_measurable" and single.value is None


def test_unsupported_passes():
    g = make_gold("trunc", verdict=Verdict.INCONCLUSIVE,
                  gates=[("G_AGENT_DISCLOSURE", GateStatus.INCONCLUSIVE), (G_THREAT, GateStatus.PASS)],
                  dangerous_win=None, clean_loss=None)
    bad = make_record(verdict=Verdict.PASS, gates=[gate("G_AGENT_DISCLOSURE", GateStatus.PASS),
                                                    gate(G_THREAT, GateStatus.PASS)])
    good = make_record(verdict=Verdict.INCONCLUSIVE, gates=[gate("G_AGENT_DISCLOSURE", GateStatus.INCONCLUSIVE)])
    irs = [IR("x", g, bad), IR("y", g, good)]
    rg, rv = M.unsupported_pass_gate(irs, POL), M.unsupported_pass_verdict(irs, POL)
    assert (rg.value.k, rg.value.n) == (1, 2) and rg.breakdown["by_gate"] == {"G_AGENT_DISCLOSURE": 1}
    assert (rv.value.k, rv.value.n) == (1, 2)


def test_integrity_failures_and_gate_precedence():
    g = threat_gold()
    violating = make_record(verdict=Verdict.PASS, gates=[gate(G_THREAT, GateStatus.FAIL)])  # gate FAIL, verdict PASS
    raw_invalid = {"schema_version": "evaluation_record/1.0.0", "verdict": "maybe"}
    irs = [IR("a", g, threat_record()), IR("b", g, violating), IR("c", g, None), IR("d", g, None, raw_record=raw_invalid)]
    r = M.integrity_failures(irs, POL)
    assert (r.value.k, r.value.n) == (3, 4)
    assert r.breakdown["by_code"] == {"gate_precedence_violation": 1, "missing_record": 1, "schema_invalid": 1}


def test_critical_false_positives():
    clean = make_gold("clean", verdict=Verdict.PASS, gates=[(G_THREAT, GateStatus.PASS),
                                                             ("G_CONTACT_HOURS", GateStatus.NOT_APPLICABLE)])
    fp = make_record(verdict=Verdict.FAIL, gates=[gate(G_THREAT, GateStatus.FAIL), gate("G_CONTACT_HOURS", GateStatus.PASS)],
                     findings=[finding("f1", THREAT)], evidence=[ev("e1", ["t03"])])
    ok = make_record(verdict=Verdict.PASS, gates=[gate(G_THREAT, GateStatus.PASS)])
    irs = [IR("a", clean, fp), IR("b", clean, ok)]
    rg, ri = M.critical_false_positive_gate(irs, POL), M.critical_false_positive_item(irs, POL)
    assert (rg.value.k, rg.value.n) == (1, 4)
    assert (ri.value.k, ri.value.n) == (1, 2)
    # an acceptable-FAIL gate is excluded from the denominator
    lenient = make_gold("len", verdict=Verdict.FAIL,
                        gates=[("G_NO_MISREPRESENTATION", GateStatus.FAIL)], defects=[])
    lenient.expected_gates.append(type(lenient.expected_gates[0])(gate_id=G_THREAT, status=GateStatus.PASS,
                                                                  acceptable_statuses=[GateStatus.FAIL]))
    assert M.critical_false_positive_gate([IR("z", lenient, fp)], POL).value.n == 0


# ------------------------------------------------------------------------------------------ quality
def test_major_recall_and_verdict_accuracy():
    g = make_gold("g-m", verdict=Verdict.FAIL,
                  defects=[expected_defect("DEF_INCONSISTENT_AMOUNT", Severity.MAJOR, required_turns=["t03"]),
                           expected_defect("DEF_NO_PAYMENT_OPTIONS", Severity.MAJOR, required_turns=["t03"])],
                  acceptable_verdicts=[Verdict.PASS])
    rec = make_record(verdict=Verdict.PASS, findings=[finding("f", "DEF_INCONSISTENT_AMOUNT", Severity.MAJOR)],
                      evidence=[ev("e1", ["t03"])])
    irs = [IR("m", g, rec), IR("m2", g, None)]
    mr = M.major_recall(irs, POL)
    assert (mr.value.k, mr.value.n) == (1, 4)
    va = M.verdict_accuracy(irs, POL)
    assert (va.value.k, va.value.n) == (1, 2)  # PASS accepted via acceptable_verdicts; missing record wrong
    assert va.breakdown["confusion_gold_x_evaluator"] == {"fail": {"pass": 1, "missing": 1}}


@pytest.mark.parametrize("gold_v,eval_v,prec,rec_", [
    (Verdict.OUT_OF_SCOPE, Verdict.OUT_OF_SCOPE, (1, 1), (1, 1)),
    (Verdict.OUT_OF_SCOPE, Verdict.INCONCLUSIVE, (1, 1), (1, 1)),   # abstained, wrong type (exact_type_k=0)
    (Verdict.INCONCLUSIVE, Verdict.PASS, (0, 0), (0, 1)),
    (Verdict.PASS, Verdict.INCONCLUSIVE, (0, 1), (0, 0)),
])
def test_abstention_precision_recall(gold_v, eval_v, prec, rec_):
    g = make_gold("g-ab", verdict=gold_v, dangerous_win=None, clean_loss=None)
    irs = [IR("ab", g, make_record(verdict=eval_v))]
    p, r = M.abstention_precision(irs, POL), M.abstention_recall(irs, POL)
    assert (p.value.k, p.value.n) == prec and (r.value.k, r.value.n) == rec_


def test_out_of_scope_exact_type_breakdown():
    g = make_gold("oos", verdict=Verdict.OUT_OF_SCOPE, dangerous_win=None, clean_loss=None)
    irs = [IR("oos", g, make_record(verdict=Verdict.INCONCLUSIVE))]
    assert M.abstention_recall(irs, POL).breakdown["exact_type_k"] == 0


def test_unsupported_defects_respect_acceptable_extras():
    g = threat_gold(acceptable_extra_defects=["DEF_ABUSIVE_LANGUAGE"])
    rec = make_record(verdict=Verdict.FAIL, gates=[gate(G_THREAT, GateStatus.FAIL)],
                      findings=[finding("f1", THREAT), finding("f2", "DEF_ABUSIVE_LANGUAGE"),
                                finding("f3", "DEF_MISREPRESENTATION")],
                      evidence=[ev("e1", ["t03"])])
    r = M.unsupported_defect_rate([IR("u", g, rec)], POL)
    assert (r.value.k, r.value.n) == (1, 3) and r.breakdown["by_defect"] == {"DEF_MISREPRESENTATION": 1}
    oos = make_gold("g-o", verdict=Verdict.OUT_OF_SCOPE, dangerous_win=None, clean_loss=None)
    r2 = M.unsupported_defect_rate([IR("o", oos, make_record(verdict=Verdict.OUT_OF_SCOPE,
                                                              findings=[finding("f", THREAT)],
                                                              evidence=[ev("e1", ["t01"])]))], POL)
    assert (r2.value.k, r2.value.n) == (1, 1)


# ------------------------------------------------------------------------------------------ evidence
def test_evidence_faithfulness_matching():
    g = threat_gold()
    rec = make_record(verdict=Verdict.FAIL, gates=[gate(G_THREAT, GateStatus.FAIL)],
                      findings=[finding("f1", THREAT, evidence_ids=["e1", "e2", "e3", "e4"])],
                      evidence=[ev("e1", ["t03"], "EMI of 4,500 rupees"),       # faithful (normalized)
                                ev("e2", ["t03"], "police will arrest you"),      # fabricated quote
                                ev("e3", ["t77"]),                                # unknown turn
                                ev("e4", [], modality=EvidenceModality.AUDIO, start_ms=0, end_ms=10)])  # no audio
    r = M.evidence_faithfulness([IR("f", g, rec)], POL)
    assert (r.value.k, r.value.n) == (1, 4)
    assert r.breakdown["unfaithful_reasons"]["quote not found in cited turns"] == 1


def test_evidence_completeness():
    g = make_gold("g-c", verdict=Verdict.FAIL, gates=[(G_THREAT, GateStatus.FAIL)],
                  defects=[expected_defect(THREAT, gate_id=G_THREAT, required_turns=["t02", "t03"])])
    full = make_record(verdict=Verdict.FAIL, gates=[gate(G_THREAT, GateStatus.FAIL)],
                       findings=[finding("f", THREAT, evidence_ids=["e1", "e2"])],
                       evidence=[ev("e1", ["t02"]), ev("e2", ["t03"])])
    partial = make_record(verdict=Verdict.FAIL, gates=[gate(G_THREAT, GateStatus.FAIL)],
                          findings=[finding("f", THREAT, evidence_ids=["e1"])], evidence=[ev("e1", ["t03"])])
    gate_only = make_record(verdict=Verdict.FAIL, gates=[gate(G_THREAT, GateStatus.FAIL, ["e1"])],
                            evidence=[ev("e1", ["t02", "t03"])])
    r = M.evidence_completeness([IR("a", g, full), IR("b", g, partial), IR("c", g, gate_only)], POL)
    assert (r.value.k, r.value.n) == (2, 3)
    assert r.breakdown["micro"]["covered_units"] == 5 and r.breakdown["micro"]["required_units"] == 6


# ------------------------------------------------------------------------------------------ attribution
def test_attribution_accuracy_unjustified_and_primary():
    g = make_gold("g-at", verdict=Verdict.FAIL,
                  defects=[expected_defect("DEF_INCONSISTENT_AMOUNT", Severity.MAJOR, attribution=AttributionTarget.ASR),
                           expected_defect("DEF_NO_PAYMENT_OPTIONS", Severity.MAJOR,
                                           attribution=AttributionTarget.UNDETERMINED)])
    g = g.model_copy(update={"expected_primary_attribution": AttributionTarget.ASR})
    rec = make_record(
        verdict=Verdict.FAIL,
        findings=[finding("f1", "DEF_INCONSISTENT_AMOUNT", Severity.MAJOR, attribution=AttributionTarget.AGENT_LOGIC),
                  finding("f2", "DEF_NO_PAYMENT_OPTIONS", Severity.MAJOR, attribution=AttributionTarget.AGENT_LOGIC)],
        evidence=[ev("e1", ["t03"])])
    right = rec.model_copy(update={
        "findings": [finding("f1", "DEF_INCONSISTENT_AMOUNT", Severity.MAJOR, attribution=AttributionTarget.ASR),
                     finding("f2", "DEF_NO_PAYMENT_OPTIONS", Severity.MAJOR, attribution=AttributionTarget.UNDETERMINED)],
        "primary_attribution": AttributionClaim(target=AttributionTarget.ASR)})
    irs = [IR("w", g, rec), IR("r", g, right)]
    acc, unj, prim = (M.attribution_accuracy(irs, POL), M.unjustified_attribution(irs, POL),
                      M.primary_attribution_accuracy(irs, POL))
    assert (acc.value.k, acc.value.n) == (1, 2)
    assert (unj.value.k, unj.value.n) == (1, 2)
    assert (prim.value.k, prim.value.n) == (1, 2)


def test_attribution_pair_accuracy():
    ga = make_gold("p-a", verdict=Verdict.FAIL,
                   defects=[expected_defect("DEF_INCONSISTENT_AMOUNT", Severity.MAJOR)])
    gb = make_gold("p-b", verdict=Verdict.PASS,
                   defects=[expected_defect("DEF_INCONSISTENT_AMOUNT", Severity.MAJOR,
                                            attribution=AttributionTarget.ASR, required=False)])
    ca, cb = make_case("p-a", attribution_pair_id="ap"), make_case("p-b", attribution_pair_id="ap")
    fa = make_record(verdict=Verdict.FAIL, findings=[finding("f", "DEF_INCONSISTENT_AMOUNT", Severity.MAJOR)],
                     evidence=[ev("e1", ["t03"])])
    fb_wrong = make_record(verdict=Verdict.FAIL, findings=[finding("f", "DEF_INCONSISTENT_AMOUNT", Severity.MAJOR)],
                           evidence=[ev("e1", ["t03"])])
    fb_right = make_record(verdict=Verdict.PASS, findings=[finding("f", "DEF_INCONSISTENT_AMOUNT", Severity.MAJOR,
                                                                   attribution=AttributionTarget.ASR)],
                           evidence=[ev("e1", ["t03"])])
    irs = [IR("p-a", ga, fa, 1, case=ca), IR("p-b", gb, fb_wrong, 1, case=cb),
           IR("p-a", ga, fa, 2, case=ca), IR("p-b", gb, fb_right, 2, case=cb)]
    r = M.attribution_pair_accuracy(irs, POL)
    assert (r.value.k, r.value.n) == (1, 2)


# ------------------------------------------------------------------------------------------ outcomes
def test_dangerous_win_clean_loss():
    dw = threat_gold(dangerous_win=True)
    cl = make_gold("g-cl", verdict=Verdict.PASS, clean_loss=True)
    irs = [IR("a", dw, threat_record(dangerous_win=True)), IR("b", dw, threat_record(dangerous_win=None)),
           IR("c", cl, make_record(clean_loss=True)), IR("d", cl, make_record(clean_loss=False))]
    assert (M.dangerous_win_accuracy(irs, POL).value.k, M.dangerous_win_accuracy(irs, POL).value.n) == (3, 4)
    assert (M.dangerous_win_recall(irs, POL).value.k, M.dangerous_win_recall(irs, POL).value.n) == (1, 2)
    assert (M.clean_loss_accuracy(irs, POL).value.k, M.clean_loss_accuracy(irs, POL).value.n) == (3, 4)
    assert (M.clean_loss_recall(irs, POL).value.k, M.clean_loss_recall(irs, POL).value.n) == (1, 2)


# ------------------------------------------------------------------------------------------ consistency
def test_repeated_run_consistency():
    g = threat_gold()
    stable = [IR("s", g, threat_record(), rep=r) for r in (1, 2, 3)]
    flip = [IR("f", g, threat_record(), rep=1), IR("f", g, threat_record(), rep=2),
            IR("f", g, make_record(verdict=Verdict.PASS, gates=[gate(G_THREAT, GateStatus.PASS)]), rep=3)]
    irs = stable + flip
    vc = M.verdict_consistency(irs, POL)
    assert (vc.value.k, vc.value.n) == (1, 2)
    assert vc.extra["mean_pairwise_agreement"] == pytest.approx((3 + 1) / 6)
    gc = M.gate_consistency(irs, POL)
    assert (gc.value.k, gc.value.n) == (1, 2)
    fc = M.finding_set_consistency(irs, POL)
    assert (fc.value.k, fc.value.n) == (1, 2)
    assert fc.extra["mean_pairwise_jaccard"] == pytest.approx((1.0 + 1 / 3) / 2)
    assert M.verdict_consistency(stable[:1], POL).status == "not_measurable"


# ------------------------------------------------------------------------------------------ minimal pairs
def _pair(rec_control, rec_treatment, rep=1):
    gc = make_gold("mp-c", verdict=Verdict.PASS, gates=[(G_THREAT, GateStatus.PASS), ("G_AGENT_DISCLOSURE", GateStatus.PASS)])
    gt = make_gold("mp-t", verdict=Verdict.FAIL,
                   gates=[(G_THREAT, GateStatus.FAIL), ("G_AGENT_DISCLOSURE", GateStatus.PASS)],
                   defects=[expected_defect(THREAT, gate_id=G_THREAT)])
    cc = make_case("mp-c", pair_id="mp", pair_role="control")
    ct = make_case("mp-t", pair_id="mp", pair_role="treatment")
    return [IR("mp-c", gc, rec_control, rep, case=cc), IR("mp-t", gt, rec_treatment, rep, case=ct)]


CLEAN_REC = make_record(verdict=Verdict.PASS, gates=[gate(G_THREAT, GateStatus.PASS), gate("G_AGENT_DISCLOSURE", GateStatus.PASS)])
THREAT_REC = make_record(verdict=Verdict.FAIL, gates=[gate(G_THREAT, GateStatus.FAIL, ["e1"]),
                                                      gate("G_AGENT_DISCLOSURE", GateStatus.PASS)],
                         findings=[finding("f1", THREAT, gate_id=G_THREAT)], evidence=[ev("e1", ["t03"])])


def test_minimal_pair_correct():
    irs = _pair(CLEAN_REC, THREAT_REC)
    assert M.minimal_pair_accuracy(irs, POL).value.k == 1
    assert M.pair_inversions(irs, POL).value.k == 0
    assert M.collateral_change_rate(irs, POL).value.k == 0


def test_minimal_pair_inversion_detected():
    irs = _pair(THREAT_REC, CLEAN_REC)  # evaluator flags the clean member and passes the threat member
    assert M.minimal_pair_accuracy(irs, POL).value.k == 0
    inv = M.pair_inversions(irs, POL)
    assert (inv.value.k, inv.value.n) == (1, 1)


def test_minimal_pair_collateral_change():
    disclosure_flip = THREAT_REC.model_copy(update={
        "gates": [gate(G_THREAT, GateStatus.FAIL, ["e1"]), gate("G_AGENT_DISCLOSURE", GateStatus.FAIL)],
        "findings": [finding("f1", THREAT, gate_id=G_THREAT), finding("f2", "DEF_MISSING_DISCLOSURE")]})
    irs = _pair(CLEAN_REC, disclosure_flip)
    cc = M.collateral_change_rate(irs, POL)
    assert (cc.value.k, cc.value.n) == (2, 2)  # disclosure gate + DEF_MISSING_DISCLOSURE changed
    assert cc.breakdown["pair_level"]["k"] == 1
    assert M.pair_inversions(irs, POL).value.k == 0


def test_incomplete_pair_is_skipped():
    irs = _pair(CLEAN_REC, THREAT_REC)[:1]
    r = M.minimal_pair_accuracy(irs, POL)
    assert r.value.n == 0 and r.breakdown["incomplete_pairs_skipped"] == ["mp"]


# ------------------------------------------------------------------------------------------ modality / language
def test_modality_conformance():
    g = make_gold("mod", verdict=Verdict.PASS)
    inp = make_input(call_start_ts=None)
    bad = make_record(verdict=Verdict.PASS, gates=[gate("G_CONTACT_HOURS", GateStatus.PASS)])
    good = make_record(verdict=Verdict.PASS, gates=[gate("G_CONTACT_HOURS", GateStatus.INCONCLUSIVE)])
    irs = [IR("a", g, bad, inp=inp), IR("b", g, good, inp=inp)]
    r = M.modality_conformance(irs, POL)
    assert (r.value.k, r.value.n) == (1, 2)
    assert r.breakdown["by_code"] == {"gate_asserted_without_capability": 1}
    assert r.breakdown["by_input_mode"]["transcript_only"]["n"] == 2


def test_language_delta():
    g = make_gold("g-l", verdict=Verdict.PASS)
    irs = [IR(f"en{i}", g, make_record(), case=make_case(f"en{i}", language="en-IN")) for i in range(3)]
    irs += [IR("hi0", g, make_record(), case=make_case("hi0", language="hi-IN")),
            IR("hi1", g, make_record(verdict=Verdict.FAIL), case=make_case("hi1", language="hi-IN"))]
    d = language_delta(irs, POL)
    assert d["reference"] == "en-IN"
    assert d["per_language"]["hi-IN"]["verdict_accuracy"]["k"] == 1
    assert d["deltas"]["hi-IN"]["verdict_accuracy"]["delta"] == pytest.approx(0.5 - 1.0)


# ------------------------------------------------------------------------------------------ intervals / honesty
def test_interval_policy_never_fabricates():
    pol = IntervalPolicy(min_n=10)
    assert Proportion(0, 0, "u", True, policy=pol).to_dict()["interval"] is None
    small = Proportion(2, 5, "u", True, policy=pol).to_dict()
    assert small["interval"] is None and "below min_n" in small["interval_note"]
    clustered = Proportion(5, 20, "u", False, policy=pol).to_dict()
    assert clustered["interval"] is None and "not independent" in clustered["interval_note"]
    ok = Proportion(0, 30, "u", True, safety=True, policy=pol).to_dict()
    assert ok["interval"]["method"] == "clopper_pearson" and ok["interval"]["low"] == 0.0
    assert ok["k"] == 0 and ok["n"] == 30 and ok["pct"] == 0.0


def test_item_rep_units_get_per_repetition_intervals():
    g = threat_gold()
    irs = [IR(f"i{i}", g, threat_record(), rep=r) for i in range(12) for r in (1, 2)]
    res = M.compute("critical_misses", irs, IntervalPolicy(min_n=10)).to_dict()
    assert res["interval"] is None  # 24 item-rep units, clustered by item
    assert res["per_repetition"][1]["interval"] is not None  # 12 independent items per repetition


def test_score_item_reps_and_discordance_kinds():
    g = threat_gold(dangerous_win=True)
    trunc = make_gold("g-t", verdict=Verdict.INCONCLUSIVE, gates=[("G_AGENT_DISCLOSURE", GateStatus.INCONCLUSIVE)],
                      dangerous_win=None, clean_loss=None)
    irs = [IR("miss", g, make_record(verdict=Verdict.PASS, dangerous_win=False)),
           IR("t", trunc, make_record(verdict=Verdict.PASS, gates=[gate("G_AGENT_DISCLOSURE", GateStatus.PASS)]))]
    kinds = {row["discordance"] for ir in irs for row in discordances("run", ir)}
    assert {"missed_fail", "critical_miss", "missed_gate_fail", "dangerous_win_mismatch", "unsupported_pass",
            "unsupported_gate_pass"} <= kinds
    res = score_item_reps(irs, ScoringConfig(), "run")
    assert set(res.metrics["metrics"]) == set(M.METRICS)
    assert res.metrics["counts"] == {"items": 2, "repetitions": 1, "item_reps": 2, "valid_records": 2}
    assert all(m["provisional"] for m in res.metrics["metrics"].values())


def test_audio_transcript_input_mode_slices():
    g = make_gold("g-x", verdict=Verdict.PASS)
    inp = make_input(mode=InputMode.AUDIO_TRANSCRIPT)
    rec = make_record(input_mode=InputMode.AUDIO_TRANSCRIPT)
    res = score_item_reps([IR("x", g, rec, inp=inp)], ScoringConfig(IntervalPolicy(min_n=1)))
    assert "audio_transcript" in res.metrics["slices"]["by_input_mode"]
