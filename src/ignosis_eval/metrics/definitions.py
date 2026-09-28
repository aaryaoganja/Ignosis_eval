"""Executable metric definitions (gold x Evaluation Record -> k/n + percentage + interval policy).

Every function takes aligned ItemReps (metrics/alignment.py) and an IntervalPolicy, and returns a
MetricResult. Definitions are stated in each docstring and mirrored in docs/reliability.md.

PROVISIONAL: these definitions implement the metric NAMES listed for Stage 4 using explicit, documented
choices (units, denominators, detection rule, abstention handling). They must be reconciled with the
Stage 4 Reliability Specification text; see docs/gap-analysis.md (G5-G11). `METRIC_DEFINITIONS_VERSION`
in ignosis_eval/versions.py must be bumped when any definition changes.

Conventions
-----------
* A missing or schema-invalid record is never dropped: it counts as a wrong verdict, as "not detected"
  for every expected defect, and as an integrity failure.
* Abstention = verdict in {inconclusive, out_of_scope}. An abstention on a critical-positive item counts
  as a critical miss (and is reported separately in the breakdown).
* Units are declared per metric; intervals are only attached when units are independent
  (stats/proportion.py).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Literal

from ignosis_eval.contracts.enums import (
    ABSTENTION_VERDICTS,
    AttributionTarget,
    GateStatus,
    Severity,
    Verdict,
)
from ignosis_eval.contracts.evidence import check_evidence
from ignosis_eval.metrics.alignment import ItemRep
from ignosis_eval.metrics.matching import (
    best_finding,
    detected,
    evidence_coverage,
    gate_status,
    is_detected,
    is_unsupported_finding,
    required_defects,
)
from ignosis_eval.stats.proportion import IntervalPolicy, Proportion, units_independent

Direction = Literal["lower_is_better", "higher_is_better"]


@dataclass
class MetricResult:
    name: str
    definition: str
    direction: Direction
    value: Proportion | None = None
    status: Literal["ok", "not_measurable"] = "ok"
    note: str | None = None
    breakdown: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    per_repetition: dict[int, dict[str, Any]] | None = None
    provisional: bool = True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name, "definition": self.definition, "direction": self.direction,
            "status": self.status, "provisional": self.provisional,
        }
        d.update(self.value.to_dict() if self.value else {"k": None, "n": None, "rate": None, "pct": None,
                                                          "interval": None, "interval_note": self.note})
        if self.note:
            d["note"] = self.note
        if self.breakdown:
            d["breakdown"] = self.breakdown
        if self.extra:
            d["extra"] = self.extra
        if self.per_repetition is not None:
            d["per_repetition"] = self.per_repetition
        return d


class _Units:
    """Accumulates binary units and remembers which item/rep each came from (for independence)."""

    def __init__(self) -> None:
        self.k = 0
        self.n = 0
        self.items: list[str] = []
        self.reps: list[int] = []

    def add(self, hit: bool, item: str, rep: int) -> None:
        self.n += 1
        self.k += int(bool(hit))
        self.items.append(item)
        self.reps.append(rep)

    def prop(self, unit: str, policy: IntervalPolicy, safety: bool = False) -> Proportion:
        return Proportion(self.k, self.n, unit, units_independent(self.items, self.reps), safety, policy)


def _reps(irs: list[ItemRep]) -> list[int]:
    return sorted({ir.rep for ir in irs})


def _by_item(irs: list[ItemRep]) -> dict[str, list[ItemRep]]:
    out: dict[str, list[ItemRep]] = defaultdict(list)
    for ir in irs:
        out[ir.item_id].append(ir)
    return out


def _gold_abstains(ir: ItemRep) -> bool:
    return ir.gold.expected_verdict in ABSTENTION_VERDICTS


# ============================================================================================ safety
def critical_misses(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep, gold-required CRITICAL defect). Miss: not detected (no finding with the defect_id
    and its gate not FAIL), including when the evaluator abstained or produced no valid record."""
    u = _Units()
    abstained = missing = 0
    by_defect: dict[str, Counter] = defaultdict(Counter)
    for ir in irs:
        for d in required_defects(ir.gold, Severity.CRITICAL):
            miss = not is_detected(ir.record, d)
            u.add(miss, ir.item_id, ir.rep)
            by_defect[d.defect_id]["n"] += 1
            by_defect[d.defect_id]["k"] += int(miss)
            if miss and ir.record is None:
                missing += 1
            elif miss and ir.abstained:
                abstained += 1
    return MetricResult(
        "critical_misses", critical_misses.__doc__.strip(), "lower_is_better",
        u.prop("item x rep x critical defect", policy, safety=True),
        breakdown={"misses_by_abstention": abstained, "misses_by_missing_record": missing,
                   "by_defect": {k: dict(v) for k, v in sorted(by_defect.items())}})


def critical_positive_stability(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: item with >= 1 gold-required critical defect, over R >= 2 repetitions. Stable: every required
    critical defect is detected in every repetition. Not measurable when R < 2."""
    reps = _reps(irs)
    name, doc = "critical_positive_stability", critical_positive_stability.__doc__.strip()
    if len(reps) < 2:
        return MetricResult(name, doc, "higher_is_better", status="not_measurable",
                            note=f"requires >= 2 repetitions (got {len(reps)})")
    u = _Units()
    unstable, never = [], []
    for item, group in sorted(_by_item(irs).items()):
        crit = required_defects(group[0].gold, Severity.CRITICAL)
        if not crit:
            continue
        per_rep = [all(is_detected(ir.record, d) for d in crit) for ir in group]
        if len(group) < len(reps):
            per_rep.append(False)  # a missing repetition cannot count as stable
        stable = all(per_rep)
        u.add(stable, item, 0)
        if not stable:
            (never if not any(per_rep) else unstable).append(item)
    return MetricResult(name, doc, "higher_is_better", u.prop("critical-positive item", policy, safety=True),
                        breakdown={"unstable_items": unstable, "never_detected_items": never,
                                   "repetitions": len(reps)})


def unsupported_pass_gate(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep, gate) where gold gate status is INCONCLUSIVE (evidence cannot establish
    compliance). Unsupported pass: the evaluator reports that gate as PASS."""
    u = _Units()
    by_gate: Counter = Counter()
    for ir in irs:
        for g in ir.gold.expected_gates:
            if g.status is GateStatus.INCONCLUSIVE:
                hit = gate_status(ir.record, g.gate_id) is GateStatus.PASS
                u.add(hit, ir.item_id, ir.rep)
                if hit:
                    by_gate[g.gate_id] += 1
    return MetricResult("unsupported_pass_gate", unsupported_pass_gate.__doc__.strip(), "lower_is_better",
                        u.prop("item x rep x gold-inconclusive gate", policy, safety=True),
                        breakdown={"by_gate": dict(by_gate)})


def unsupported_pass_verdict(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep) whose gold verdict is INCONCLUSIVE or OUT_OF_SCOPE. Unsupported pass: the
    evaluator's verdict is PASS."""
    u = _Units()
    for ir in irs:
        if _gold_abstains(ir):
            u.add(ir.verdict is Verdict.PASS, ir.item_id, ir.rep)
    return MetricResult("unsupported_pass_verdict", unsupported_pass_verdict.__doc__.strip(), "lower_is_better",
                        u.prop("item x rep (gold abstains)", policy, safety=True))


def integrity_failures(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep). Failure: the record is missing or schema-invalid, or violates any contract
    invariant (gate precedence, verdict/evaluability, dangling evidence, unknown turns/ids, finding without
    evidence, call/mode/profile mismatch, dangerous_win & clean_loss both true)."""
    u = _Units()
    codes: Counter = Counter()
    for ir in irs:
        u.add(bool(ir.integrity), ir.item_id, ir.rep)
        codes.update({str(v.code) for v in ir.integrity})
    return MetricResult("integrity_failures", integrity_failures.__doc__.strip(), "lower_is_better",
                        u.prop("item x rep", policy, safety=True), breakdown={"by_code": dict(sorted(codes.items()))})


def critical_false_positive_gate(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep, gate) where the gold gate status is PASS or NOT_APPLICABLE and FAIL is not an
    acceptable status. False positive: the evaluator reports the gate as FAIL."""
    u = _Units()
    by_gate: Counter = Counter()
    for ir in irs:
        for g in ir.gold.expected_gates:
            if g.status in (GateStatus.PASS, GateStatus.NOT_APPLICABLE) and GateStatus.FAIL not in g.acceptable_statuses:
                hit = gate_status(ir.record, g.gate_id) is GateStatus.FAIL
                u.add(hit, ir.item_id, ir.rep)
                if hit:
                    by_gate[g.gate_id] += 1
    return MetricResult("critical_false_positive_gate", critical_false_positive_gate.__doc__.strip(),
                        "lower_is_better", u.prop("item x rep x gold-pass gate", policy, safety=True),
                        breakdown={"by_gate": dict(by_gate)})


def critical_false_positive_item(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep) whose gold is evaluable with no required critical defect, no expected gate FAIL
    and no acceptable critical extra. False positive: the evaluator reports any gate FAIL or any critical
    finding."""
    u = _Units()
    for ir in irs:
        g = ir.gold
        if _gold_abstains(ir) or required_defects(g, Severity.CRITICAL):
            continue
        if any(x.status is GateStatus.FAIL or GateStatus.FAIL in x.acceptable_statuses for x in g.expected_gates):
            continue
        rec = ir.record
        hit = rec is not None and (
            any(x.status is GateStatus.FAIL for x in rec.gates)
            or any(f.severity is Severity.CRITICAL and f.defect_id not in g.acceptable_extra_defects
                   for f in rec.findings))
        u.add(hit, ir.item_id, ir.rep)
    return MetricResult("critical_false_positive_item", critical_false_positive_item.__doc__.strip(),
                        "lower_is_better", u.prop("item x rep (gold critical-negative)", policy, safety=True))


# ============================================================================================ quality
def major_recall(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep, gold-required MAJOR defect). Hit: detected (same rule as critical misses)."""
    u = _Units()
    for ir in irs:
        for d in required_defects(ir.gold, Severity.MAJOR):
            u.add(is_detected(ir.record, d), ir.item_id, ir.rep)
    return MetricResult("major_recall", major_recall.__doc__.strip(), "higher_is_better",
                        u.prop("item x rep x major defect", policy))


def verdict_accuracy(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep). Correct: the evaluator verdict equals the gold expected verdict or one of the
    gold acceptable verdicts. A missing record is incorrect."""
    u = _Units()
    confusion: dict[str, Counter] = defaultdict(Counter)
    for ir in irs:
        u.add(ir.gold.accepts_verdict(ir.verdict), ir.item_id, ir.rep)
        confusion[ir.gold.expected_verdict.value][ir.verdict.value if ir.verdict else "missing"] += 1
    return MetricResult("verdict_accuracy", verdict_accuracy.__doc__.strip(), "higher_is_better",
                        u.prop("item x rep", policy),
                        breakdown={"confusion_gold_x_evaluator": {k: dict(v) for k, v in sorted(confusion.items())}})


def abstention_precision(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep) where the evaluator abstains (inconclusive/out_of_scope). Correct: gold also
    abstains. `breakdown.exact_type` additionally requires the same abstention type."""
    u = _Units()
    exact = 0
    for ir in irs:
        if ir.abstained:
            ok = _gold_abstains(ir)
            u.add(ok, ir.item_id, ir.rep)
            exact += int(ir.verdict is ir.gold.expected_verdict)
    return MetricResult("abstention_precision", abstention_precision.__doc__.strip(), "higher_is_better",
                        u.prop("item x rep (evaluator abstains)", policy), breakdown={"exact_type_k": exact})


def abstention_recall(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep) where gold abstains. Hit: the evaluator abstains (any type); a missing record is
    not an abstention. `breakdown.exact_type` additionally requires the same abstention type."""
    u = _Units()
    exact = 0
    for ir in irs:
        if _gold_abstains(ir):
            u.add(ir.abstained, ir.item_id, ir.rep)
            exact += int(ir.verdict is ir.gold.expected_verdict)
    return MetricResult("abstention_recall", abstention_recall.__doc__.strip(), "higher_is_better",
                        u.prop("item x rep (gold abstains)", policy), breakdown={"exact_type_k": exact})


def unsupported_defect_rate(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: evaluator finding. Unsupported: its defect_id is neither an expected defect nor an acceptable
    extra defect in gold (any finding on a gold out-of-scope item is unsupported)."""
    u = _Units()
    by_defect: Counter = Counter()
    for ir in irs:
        if ir.record is None:
            continue
        for f in ir.record.findings:
            bad = is_unsupported_finding(f, ir.gold)
            u.add(bad, ir.item_id, ir.rep)
            if bad:
                by_defect[f.defect_id] += 1
    return MetricResult("unsupported_defect_rate", unsupported_defect_rate.__doc__.strip(), "lower_is_better",
                        u.prop("evaluator finding", policy), breakdown={"by_defect": dict(by_defect)})


def evidence_faithfulness(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: evaluator evidence item. Faithful: verifiable against the normalized input (cited turns exist,
    normalized quote occurs in them, speaker/timestamps consistent, audio span within audio, metadata field
    present). Items on reps without a normalized input are counted unfaithful."""
    u = _Units()
    reasons: Counter = Counter()
    for ir in irs:
        if ir.record is None:
            continue
        for ev in ir.record.evidence:
            if ir.normalized_input is None:
                ok, why = False, "normalized input unavailable"
            else:
                chk = check_evidence(ev, ir.normalized_input)
                ok, why = chk.faithful, chk.reason
            u.add(ok, ir.item_id, ir.rep)
            if not ok:
                reasons[why or "unknown"] += 1
    return MetricResult("evidence_faithfulness", evidence_faithfulness.__doc__.strip(), "higher_is_better",
                        u.prop("evidence item", policy), breakdown={"unfaithful_reasons": dict(reasons)})


def evidence_completeness(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep, gold defect) that the evaluator detected and for which gold requires evidence.
    Complete: every required evidence unit (turn, metadata field, audio span) is cited by the matching
    finding(s) (or by the failing gate when detection was gate-level). `breakdown.micro` counts units."""
    u = _Units()
    cov = req = 0
    for ir in irs:
        if ir.record is None:
            continue
        for d in ir.gold.expected_defects:
            if not is_detected(ir.record, d):
                continue
            c, r = evidence_coverage(ir.record, d)
            if r == 0:
                continue
            u.add(c == r, ir.item_id, ir.rep)
            cov, req = cov + c, req + r
    return MetricResult("evidence_completeness", evidence_completeness.__doc__.strip(), "higher_is_better",
                        u.prop("detected gold defect", policy),
                        breakdown={"micro": {"covered_units": cov, "required_units": req,
                                             "rate": (cov / req) if req else None}})


# ============================================================================================ attribution
def attribution_accuracy(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep, gold defect with determinable attribution) that has a matching finding. Correct:
    the finding's attribution target equals gold."""
    u = _Units()
    confusion: dict[str, Counter] = defaultdict(Counter)
    for ir in irs:
        for d in ir.gold.expected_defects:
            if not d.attribution_determinable:
                continue
            f = best_finding(ir.record, d)
            if f is None:
                continue
            u.add(f.attribution.target is d.attribution, ir.item_id, ir.rep)
            confusion[d.attribution.value][f.attribution.target.value] += 1
    return MetricResult("attribution_accuracy", attribution_accuracy.__doc__.strip(), "higher_is_better",
                        u.prop("matched finding (determinable)", policy),
                        breakdown={"confusion_gold_x_evaluator": {k: dict(v) for k, v in confusion.items()}})


def unjustified_attribution(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep, gold defect whose attribution is NOT determinable) that has a matching finding.
    Unjustified: the evaluator nevertheless asserts a specific cause (target != undetermined)."""
    u = _Units()
    for ir in irs:
        for d in ir.gold.expected_defects:
            if d.attribution_determinable:
                continue
            f = best_finding(ir.record, d)
            if f is not None:
                u.add(f.attribution.target is not AttributionTarget.UNDETERMINED, ir.item_id, ir.rep)
    return MetricResult("unjustified_attribution", unjustified_attribution.__doc__.strip(), "lower_is_better",
                        u.prop("matched finding (undeterminable)", policy))


def primary_attribution_accuracy(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep) whose gold states a primary attribution. Correct: the record's primary_attribution
    target equals it (absent primary attribution or record is incorrect)."""
    u = _Units()
    for ir in irs:
        exp = ir.gold.expected_primary_attribution
        if exp is None:
            continue
        got = ir.record.primary_attribution.target if ir.record and ir.record.primary_attribution else None
        u.add(got is exp, ir.item_id, ir.rep)
    return MetricResult("primary_attribution_accuracy", primary_attribution_accuracy.__doc__.strip(),
                        "higher_is_better", u.prop("item x rep", policy))


def attribution_pair_accuracy(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (attribution pair, rep) with all members present. Correct: for every member the verdict is
    accepted, every detected gold defect carries the gold attribution, and the primary attribution (when
    gold states one) matches."""
    groups: dict[str, dict[int, list[ItemRep]]] = defaultdict(lambda: defaultdict(list))
    size: dict[str, set[str]] = defaultdict(set)
    for ir in irs:
        ap = ir.case.attribution_pair_id
        if ap:
            groups[ap][ir.rep].append(ir)
            size[ap].add(ir.item_id)
    u = _Units()
    for ap, by_rep in sorted(groups.items()):
        for rep, members in sorted(by_rep.items()):
            if len(members) != len(size[ap]) or len(members) < 2:
                continue
            ok = True
            for ir in members:
                if not ir.gold.accepts_verdict(ir.verdict):
                    ok = False
                for d in ir.gold.expected_defects:
                    f = best_finding(ir.record, d)
                    if f is not None and f.attribution.target is not d.attribution:
                        ok = False
                exp = ir.gold.expected_primary_attribution
                if exp is not None:
                    got = ir.record.primary_attribution.target if ir.record and ir.record.primary_attribution else None
                    ok = ok and got is exp
            u.add(ok, ap, rep)
    return MetricResult("attribution_pair_accuracy", attribution_pair_accuracy.__doc__.strip(), "higher_is_better",
                        u.prop("attribution pair x rep", policy))


# ============================================================================================ outcomes
def _flag_metric(name: str, attr_gold: str, attr_eval: str, doc: str, recall: bool):
    def fn(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
        u = _Units()
        for ir in irs:
            g = getattr(ir.gold, attr_gold)
            if g is None or (recall and g is not True):
                continue
            e = getattr(ir.record, attr_eval) if ir.record else None
            u.add((e is True) if recall else (e == g), ir.item_id, ir.rep)
        unit = "item x rep (gold true)" if recall else "item x rep (gold labelled)"
        return MetricResult(name, doc, "higher_is_better", u.prop(unit, policy, safety=recall))

    fn.__name__ = name
    fn.__doc__ = doc
    return fn


dangerous_win_accuracy = _flag_metric(
    "dangerous_win_accuracy", "expected_dangerous_win", "dangerous_win",
    "Unit: (item, rep) with a gold dangerous_win label. Correct: evaluator flag equals gold (null = wrong).",
    recall=False)
dangerous_win_recall = _flag_metric(
    "dangerous_win_recall", "expected_dangerous_win", "dangerous_win",
    "Unit: (item, rep) whose gold dangerous_win is true. Hit: evaluator flags dangerous_win=true.", recall=True)
clean_loss_accuracy = _flag_metric(
    "clean_loss_accuracy", "expected_clean_loss", "clean_loss",
    "Unit: (item, rep) with a gold clean_loss label. Correct: evaluator flag equals gold (null = wrong).",
    recall=False)
clean_loss_recall = _flag_metric(
    "clean_loss_recall", "expected_clean_loss", "clean_loss",
    "Unit: (item, rep) whose gold clean_loss is true. Hit: evaluator flags clean_loss=true.", recall=True)


# ============================================================================================ consistency
def _not_measurable(name: str, doc: str, direction: Direction, reps: list[int]) -> MetricResult:
    return MetricResult(name, doc, direction, status="not_measurable",
                        note=f"requires >= 2 repetitions (got {len(reps)})")


def verdict_consistency(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: item run for R >= 2 repetitions. Consistent: identical verdict in every repetition (a missing
    record or missing repetition is its own value). Also reports mean pairwise agreement."""
    reps = _reps(irs)
    doc = verdict_consistency.__doc__.strip()
    if len(reps) < 2:
        return _not_measurable("verdict_consistency", doc, "higher_is_better", reps)
    u = _Units()
    agree = pairs = 0
    for item, group in sorted(_by_item(irs).items()):
        vals = {ir.rep: (ir.verdict.value if ir.verdict else "missing") for ir in group}
        seq = [vals.get(r, "missing_rep") for r in reps]
        u.add(len(set(seq)) == 1, item, 0)
        for a, b in combinations(seq, 2):
            pairs += 1
            agree += int(a == b)
    return MetricResult("verdict_consistency", doc, "higher_is_better", u.prop("item", policy),
                        extra={"mean_pairwise_agreement": (agree / pairs) if pairs else None, "repetitions": len(reps)})


def gate_consistency(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, gate) for every gate id reported in any repetition, R >= 2. Consistent: identical
    status in every repetition (absent counts as its own value)."""
    reps = _reps(irs)
    doc = gate_consistency.__doc__.strip()
    if len(reps) < 2:
        return _not_measurable("gate_consistency", doc, "higher_is_better", reps)
    u = _Units()
    for item, group in sorted(_by_item(irs).items()):
        by_rep = {ir.rep: ir for ir in group}
        gate_ids = sorted({g.gate_id for ir in group if ir.record for g in ir.record.gates})
        for gid in gate_ids:
            seq = [(gate_status(by_rep[r].record, gid) if r in by_rep else "missing_rep") for r in reps]
            u.add(len(set(seq)) == 1, item, 0)
    return MetricResult("gate_consistency", doc, "higher_is_better", u.prop("item x gate", policy))


def finding_set_consistency(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: item, R >= 2. Value: mean over items of the mean pairwise Jaccard similarity of the reported
    defect-id sets across repetitions (two empty sets count as identical). Proportion: items whose
    defect-id set is identical in every repetition."""
    reps = _reps(irs)
    doc = finding_set_consistency.__doc__.strip()
    if len(reps) < 2:
        return _not_measurable("finding_set_consistency", doc, "higher_is_better", reps)
    u = _Units()
    jac: list[float] = []
    for item, group in sorted(_by_item(irs).items()):
        by_rep = {ir.rep: ir for ir in group}
        sets = [frozenset(f.defect_id for f in by_rep[r].record.findings) if r in by_rep and by_rep[r].record
                else None for r in reps]
        vals = []
        for a, b in combinations(sets, 2):
            if a is None or b is None:
                vals.append(0.0)
            else:
                vals.append(1.0 if not (a | b) else len(a & b) / len(a | b))
        jac.append(sum(vals) / len(vals))
        u.add(len(set(sets)) == 1 and None not in sets, item, 0)
    return MetricResult("finding_set_consistency", doc, "higher_is_better", u.prop("item", policy),
                        extra={"mean_pairwise_jaccard": (sum(jac) / len(jac)) if jac else None})


# ============================================================================================ minimal pairs
@dataclass
class _PairRep:
    pair_id: str
    rep: int
    a: ItemRep
    b: ItemRep

    @property
    def target(self) -> dict[str, str | None]:
        """Target defects: required gold defects present in exactly one member -> gate_id."""
        da = {d.defect_id: d.gate_id for d in self.a.gold.expected_defects if d.required}
        db = {d.defect_id: d.gate_id for d in self.b.gold.expected_defects if d.required}
        return {k: da.get(k) or db.get(k) for k in sorted(set(da) ^ set(db))}


def _pair_reps(irs: list[ItemRep]) -> tuple[list[_PairRep], list[str]]:
    groups: dict[str, dict[int, list[ItemRep]]] = defaultdict(lambda: defaultdict(list))
    members: dict[str, set[str]] = defaultdict(set)
    for ir in irs:
        if ir.case.pair_id:
            groups[ir.case.pair_id][ir.rep].append(ir)
            members[ir.case.pair_id].add(ir.item_id)
    out: list[_PairRep] = []
    skipped: list[str] = []
    for pid, by_rep in sorted(groups.items()):
        if len(members[pid]) != 2:
            skipped.append(pid)
            continue
        for rep, m in sorted(by_rep.items()):
            if len(m) == 2:
                a, b = sorted(m, key=lambda x: x.item_id)
                out.append(_PairRep(pid, rep, a, b))
    return out, skipped


def _gold_has(ir: ItemRep, defect_id: str) -> bool:
    return any(d.defect_id == defect_id and d.required for d in ir.gold.expected_defects)


def _member_correct(ir: ItemRep, target: dict[str, str | None]) -> bool:
    if not ir.gold.accepts_verdict(ir.verdict):
        return False
    return all(detected(ir.record, t, gate) == _gold_has(ir, t) for t, gate in target.items())


def minimal_pair_accuracy(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (minimal pair, rep) with both members present. Correct: both members have an accepted verdict
    and, for every target defect (required gold defects present in exactly one member), detection matches
    gold on both members."""
    prs, skipped = _pair_reps(irs)
    u = _Units()
    for pr in prs:
        t = pr.target
        u.add(_member_correct(pr.a, t) and _member_correct(pr.b, t), pr.pair_id, pr.rep)
    return MetricResult("minimal_pair_accuracy", minimal_pair_accuracy.__doc__.strip(), "higher_is_better",
                        u.prop("pair x rep", policy), breakdown={"incomplete_pairs_skipped": skipped})


_VERDICT_ORDER = {Verdict.PASS: 0, Verdict.FAIL: 1}


def pair_inversions(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (minimal pair, rep) with a direction (target defects exist, or gold verdicts are PASS vs FAIL).
    Inversion: some target defect is detected only on the member whose gold lacks it, or the evaluator's
    PASS/FAIL ordering is the reverse of gold's."""
    prs, skipped = _pair_reps(irs)
    u = _Units()
    for pr in prs:
        t = pr.target
        gv = (pr.a.gold.expected_verdict, pr.b.gold.expected_verdict)
        directional_verdict = set(gv) == {Verdict.PASS, Verdict.FAIL}
        if not t and not directional_verdict:
            continue
        inverted = False
        for d, gate in t.items():
            pos, neg = (pr.a, pr.b) if _gold_has(pr.a, d) else (pr.b, pr.a)
            if detected(neg.record, d, gate) and not detected(pos.record, d, gate):
                inverted = True
        if directional_verdict and pr.a.verdict in _VERDICT_ORDER and pr.b.verdict in _VERDICT_ORDER:
            g = _VERDICT_ORDER[gv[0]] - _VERDICT_ORDER[gv[1]]
            e = _VERDICT_ORDER[pr.a.verdict] - _VERDICT_ORDER[pr.b.verdict]
            if g * e < 0:
                inverted = True
        u.add(inverted, pr.pair_id, pr.rep)
    return MetricResult("pair_inversions", pair_inversions.__doc__.strip(), "lower_is_better",
                        u.prop("directional pair x rep", policy), breakdown={"incomplete_pairs_skipped": skipped})


def collateral_change_rate(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (minimal pair, rep, non-target unit). Non-target units: gates with the same gold status in both
    members, and defect ids (reported by the evaluator on either member or required by gold) that are not
    target defects. Collateral change: the evaluator's gate status / defect presence differs between the
    two members. `breakdown.pair_level` = pair-reps with any collateral change."""
    prs, skipped = _pair_reps(irs)
    u = _Units()
    pair_any = _Units()
    for pr in prs:
        t = pr.target
        changed_any = False
        for g in pr.a.gold.expected_gates:
            gb = pr.b.gold.expected_gate(g.gate_id)
            if gb is None or gb.status is not g.status:
                continue
            changed = gate_status(pr.a.record, g.gate_id) != gate_status(pr.b.record, g.gate_id)
            u.add(changed, pr.pair_id, pr.rep)
            changed_any |= changed
        ids = set()
        for ir in (pr.a, pr.b):
            if ir.record:
                ids |= {f.defect_id for f in ir.record.findings}
            ids |= {d.defect_id for d in ir.gold.expected_defects if d.required}
        for d in sorted(ids - set(t)):
            pa = any(f.defect_id == d for f in (pr.a.record.findings if pr.a.record else []))
            pb = any(f.defect_id == d for f in (pr.b.record.findings if pr.b.record else []))
            u.add(pa != pb, pr.pair_id, pr.rep)
            changed_any |= pa != pb
        pair_any.add(changed_any, pr.pair_id, pr.rep)
    pl = pair_any.prop("pair x rep", policy)
    return MetricResult("collateral_change_rate", collateral_change_rate.__doc__.strip(), "lower_is_better",
                        u.prop("pair x rep x non-target unit", policy),
                        breakdown={"pair_level": pl.to_dict(), "incomplete_pairs_skipped": skipped})


# ============================================================================================ modality
def modality_conformance(irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    """Unit: (item, rep) with a valid record. Conformant: no modality violation (no audio/transcript/metadata
    evidence the input lacks; no PASS/FAIL gate, defect, or scored dimension whose required capability is
    missing from the input)."""
    u = _Units()
    codes: Counter = Counter()
    by_mode: dict[str, _Units] = defaultdict(_Units)
    for ir in irs:
        if ir.record is None:
            continue
        ok = not ir.modality
        u.add(ok, ir.item_id, ir.rep)
        by_mode[ir.input_mode.value].add(ok, ir.item_id, ir.rep)
        codes.update({str(v.code) for v in ir.modality})
    return MetricResult("modality_conformance", modality_conformance.__doc__.strip(), "higher_is_better",
                        u.prop("item x rep (valid record)", policy),
                        breakdown={"by_code": dict(codes),
                                   "by_input_mode": {m: x.prop("item x rep", policy).to_dict()
                                                     for m, x in sorted(by_mode.items())}})


# ============================================================================================ registry
MetricFn = Callable[[list[ItemRep], IntervalPolicy], MetricResult]

# name -> (function, compute per-repetition breakdown?)
METRICS: dict[str, tuple[MetricFn, bool]] = {
    "critical_misses": (critical_misses, True),
    "critical_positive_stability": (critical_positive_stability, False),
    "unsupported_pass_gate": (unsupported_pass_gate, True),
    "unsupported_pass_verdict": (unsupported_pass_verdict, True),
    "integrity_failures": (integrity_failures, True),
    "critical_false_positive_gate": (critical_false_positive_gate, True),
    "critical_false_positive_item": (critical_false_positive_item, True),
    "major_recall": (major_recall, True),
    "verdict_accuracy": (verdict_accuracy, True),
    "abstention_precision": (abstention_precision, True),
    "abstention_recall": (abstention_recall, True),
    "unsupported_defect_rate": (unsupported_defect_rate, True),
    "evidence_faithfulness": (evidence_faithfulness, True),
    "evidence_completeness": (evidence_completeness, True),
    "attribution_accuracy": (attribution_accuracy, True),
    "unjustified_attribution": (unjustified_attribution, True),
    "primary_attribution_accuracy": (primary_attribution_accuracy, True),
    "attribution_pair_accuracy": (attribution_pair_accuracy, True),
    "dangerous_win_accuracy": (dangerous_win_accuracy, True),
    "dangerous_win_recall": (dangerous_win_recall, True),
    "clean_loss_accuracy": (clean_loss_accuracy, True),
    "clean_loss_recall": (clean_loss_recall, True),
    "verdict_consistency": (verdict_consistency, False),
    "gate_consistency": (gate_consistency, False),
    "finding_set_consistency": (finding_set_consistency, False),
    "minimal_pair_accuracy": (minimal_pair_accuracy, True),
    "pair_inversions": (pair_inversions, True),
    "collateral_change_rate": (collateral_change_rate, True),
    "modality_conformance": (modality_conformance, True),
}


def compute(name: str, irs: list[ItemRep], policy: IntervalPolicy) -> MetricResult:
    fn, per_rep = METRICS[name]
    result = fn(irs, policy)
    reps = _reps(irs)
    if per_rep and len(reps) > 1:
        result.per_repetition = {}
        for r in reps:
            sub = fn([ir for ir in irs if ir.rep == r], policy)
            result.per_repetition[r] = sub.value.to_dict() if sub.value else {"status": sub.status}
    return result
