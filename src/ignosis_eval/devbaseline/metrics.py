"""Intent-referenced DEV baseline metrics (docs/dev-baseline.md). Every metric is either a number with its
denominator or the string UNMEASURED with the reason; nothing is filled in.

Reference per scored DEV item (frozen design intent, not gold): verdict, evaluability, gate statuses (fired =
FAIL or INCONCLUSIVE+trigger), intended non-gate finding codes with their attribution (T), control must-not-fire
gates, pairs (MP-01 / MP-04 / MP-10 and their target check), label confidence, contested flag and ambiguity note.

Per system, over its records (majority over repetitions where k > 1):
  verdict_accuracy        majority verdict == intended verdict
  critical_recall         (item, gate) intended fired -> majority fired
  must_not_fire_precision control (item, gate) must-not-fire targets -> majority NOT fired
  gate_false_fires        (item, gate) intended not fired -> majority fired (count)
  defect_precision/recall intended non-gate codes vs majority-emitted ASSERTED codes (code level; the design's beat
                          anchors are not mapped to turns yet, so no anchor matching)
  evidence_faithfulness   cited evidence of fired gates and ASSERTED findings that verifies against the unit's own
                          normalized input (turn exists, role matches, quote_score >= quote_match_min); no reference
  abstention              EVALUATION_FAILED, NOT_EVALUABLE, PARTIAL counts; evaluability agreement with the design
  attribution_agreement   detected intended findings / gates: record attribution == design attribution (T)
  dangerous_win_agreement majority DW tag == intended DW
  clean_loss_agreement    majority Clean Loss tag == intended Clean Loss (blueprint `clean_loss`)
  pair_accuracy           SD-20 on the design pairs: gate target -> violating fired and clean not fired; code target
                          -> violating emitted and clean not emitted (majority); inversions counted
  consistency             k > 1 only: items whose reps agree on the verdict, and whose record content hashes match
  latency                 p50 / p95 (nearest rank) of per-(unit, rep) latency_s (A+: derivation_s)
  cost                    token sums; USD only when prices are supplied (the price snapshot is B-08)
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ignosis_eval.benchmark.public_dev import DATA_FILES, DevDesign
from ignosis_eval.contracts.blueprint import Blueprint
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, Evidence
from ignosis_eval.contracts.evidence import quote_score
from ignosis_eval.contracts.run_manifest import (
    DERIVATION_LOG_FILE,
    NORMALIZED_INPUT_FILE,
    RECORD_FILE,
    TIMING_FILE,
    USAGE_FILE,
)

UNMEASURED = "UNMEASURED"
FIRED = ("FAIL", "INCONCLUSIVE+trigger")
SYSTEM_ORDER = ("K0", "A", "A+", "B")


@dataclass
class ItemRef:
    item_id: str
    pack: str
    verdict: str
    evaluability: str
    gates: dict[str, str]
    codes: dict[str, str]  # intended non-gate code -> attribution (T)
    gate_attr: dict[str, str]  # intended fired gate -> attribution (T)
    control_gates: list[str]
    pair: tuple[str, str] | None
    pair_target: str | None  # the pair's target check (design registries)
    dangerous_win: str
    label_confidence: str
    contested: bool
    ambiguity: str
    clean_loss: bool = False

    @property
    def fired(self) -> set[str]:
        return {g for g, s in self.gates.items() if s in FIRED}

    @property
    def ambiguous(self) -> bool:
        return self.label_confidence != "Sure" or self.contested or self.ambiguity not in ("-", "")


def build_reference(design: DevDesign, public_dir: Path, items: list[str]) -> dict[str, ItemRef]:
    bp = Blueprint.model_validate(yaml.safe_load((public_dir / DATA_FILES["blueprint"]).read_text(encoding="utf-8")))
    raw = {i.item_id: i for i in bp.items}
    targets = {p.pair_id: p.target_check for p in design.registries().pairs}
    out: dict[str, ItemRef] = {}
    for iid in items:
        it, b = design.items[iid], raw[iid]
        gates = dict(it.gates or {})
        codes = {f.code: f.attribution_T for f in it.findings if f.code not in gates}
        gate_attr = {f.code: f.attribution_T for f in it.findings if f.code in gates}
        out[iid] = ItemRef(iid, it.pack.value, it.verdict, it.evaluability, gates, codes, gate_attr,
                           list(it.control_target_gates), it.pair, targets.get(it.pair[0]) if it.pair else None,
                           it.dangerous_win, b.label_confidence, b.contested, b.ambiguity_notes, b.clean_loss)
    return out


# ------------------------------------------------------------------------------------------ run loading
@dataclass
class Unit:
    records: list[EvaluationRecord] = field(default_factory=list)
    ni: NormalizedInput | None = None
    latency: list[float] = field(default_factory=list)
    usage: Counter = field(default_factory=Counter)
    derivation: list[dict] = field(default_factory=list)


def load_run(run_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Unit]]]:
    manifest = json.loads((run_dir / "draft_run.json").read_text(encoding="utf-8"))
    by_system: dict[str, dict[str, Unit]] = {}
    for sdir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        for idir in sorted(p for p in sdir.iterdir() if p.is_dir()):
            u = Unit()
            for rdir in sorted(idir.glob("*/r*")):
                rec_p = rdir / RECORD_FILE
                if rec_p.exists():
                    u.records.append(EvaluationRecord.model_validate_json(rec_p.read_text(encoding="utf-8")))
                if u.ni is None and (rdir / NORMALIZED_INPUT_FILE).exists():
                    u.ni = NormalizedInput.model_validate_json((rdir / NORMALIZED_INPUT_FILE).read_text("utf-8"))
                t = json.loads((rdir / TIMING_FILE).read_text(encoding="utf-8"))
                u.latency.append(float(t.get("latency_s", t.get("derivation_s", 0.0))))
                u.usage.update({k: int(v) for k, v in json.loads((rdir / USAGE_FILE).read_text("utf-8")).items()})
                d = rdir / DERIVATION_LOG_FILE
                if d.exists():
                    u.derivation.extend(json.loads(d.read_text(encoding="utf-8")).get("entries", []))
            by_system.setdefault(sdir.name, {})[idir.name] = u
    return manifest, by_system


# ------------------------------------------------------------------------------------------ record views
def ok(rec: EvaluationRecord) -> bool:
    return rec.record_status.value == "OK"


def fired(rec: EvaluationRecord) -> set[str]:
    return {g.gate.value for g in rec.gates if g.status.value == "FAIL"} if ok(rec) else set()


def emitted(rec: EvaluationRecord) -> set[str]:
    return {f.code for f in rec.findings if f.finding_state.value == "ASSERTED"} if ok(rec) else set()


def majority(values: list[Any]) -> Any:
    if not values:
        return None
    (top, n), *rest = Counter(values).most_common()
    return top if not rest or rest[0][1] < n else "NO_MAJORITY"


def majority_set(sets: list[set[str]]) -> set[str]:
    k = len(sets)
    counts = Counter(x for s in sets for x in s)
    return {x for x, n in counts.items() if n > k / 2}


def verdict_of(u: Unit) -> str | None:
    return majority([r.verdict.value if r.verdict else "EVALUATION_FAILED" for r in u.records])


def ratio(num: int, den: int) -> dict[str, Any]:
    return {"value": round(num / den, 4) if den else UNMEASURED, "num": num, "den": den}


def nearest_rank(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return v[max(0, math.ceil(p / 100 * len(v)) - 1)]


def verify(ev: Evidence, ni: NormalizedInput, threshold: float) -> bool:
    if ev.header_field is not None:
        return ni.header.call_start_ts is not None
    t = ni.turn_by_number(ev.turn) if ev.turn else None
    if t is None or ev.role is not t.role:
        return False
    return quote_score(ev.quote or "", t.text) >= threshold


def cited(rec: EvaluationRecord) -> list[Evidence]:
    if not ok(rec):
        return []
    out = [e for g in rec.gates if g.status.value in ("FAIL", "INCONCLUSIVE") for e in g.evidence]
    return out + [e for f in rec.findings if f.finding_state.value == "ASSERTED" for e in f.evidence]


# ------------------------------------------------------------------------------------------ metrics
def system_metrics(refs: dict[str, ItemRef], units: dict[str, Unit], *, threshold: float,
                   prices: tuple[float, float] | None) -> dict[str, Any]:
    items = [i for i in refs if i in units and units[i].records]
    if not items:
        return {"status": "NOT_EXECUTED"}
    m: dict[str, Any] = {"status": "EXECUTED", "items": len(items),
                         "records": sum(len(units[i].records) for i in items),
                         "repetitions": max(len(units[i].records) for i in items)}
    vr = {i: verdict_of(units[i]) for i in items}
    fr = {i: majority_set([fired(r) for r in units[i].records]) for i in items}
    em = {i: majority_set([emitted(r) for r in units[i].records]) for i in items}
    m["verdict_accuracy"] = ratio(sum(vr[i] == refs[i].verdict for i in items), len(items))
    pairs_g = [(i, g) for i in items for g in refs[i].fired]
    m["critical_recall"] = ratio(sum(g in fr[i] for i, g in pairs_g), len(pairs_g))
    m["critical_recall"]["by_gate"] = {g: ratio(sum(g in fr[i] for i, gg in pairs_g if gg == g),
                                                sum(1 for _, gg in pairs_g if gg == g))
                                       for g in sorted({g for _, g in pairs_g})}
    mnf = [(i, g) for i in items for g in refs[i].control_gates]
    m["must_not_fire_precision"] = ratio(sum(g not in fr[i] for i, g in mnf), len(mnf))
    m["gate_false_fires"] = sorted(f"{i}:{g}" for i in items for g in fr[i] if g not in refs[i].fired)
    tp = sum(len(set(refs[i].codes) & em[i]) for i in items)
    fp = sum(len(em[i] - set(refs[i].codes)) for i in items)
    fn = sum(len(set(refs[i].codes) - em[i]) for i in items)
    m["defect_precision"], m["defect_recall"] = ratio(tp, tp + fp), ratio(tp, tp + fn)
    evs = [(e, units[i].ni) for i in items for r in units[i].records for e in cited(r) if units[i].ni is not None]
    m["evidence_faithfulness"] = ratio(sum(verify(e, ni, threshold) for e, ni in evs if ni), len(evs))
    recs = [r for i in items for r in units[i].records]
    ev_agree = sum(majority([r.evaluability.status.value if r.evaluability else "EVALUATION_FAILED"
                             for r in units[i].records]) == refs[i].evaluability for i in items)
    m["abstention"] = {"evaluation_failed": sum(not ok(r) for r in recs), "records": len(recs),
                       "not_evaluable": sum(ok(r) and r.verdict.value == "NOT_EVALUABLE" for r in recs),  # type: ignore[union-attr]
                       "partial": sum(ok(r) and r.evaluability.status.value == "PARTIAL" for r in recs),  # type: ignore[union-attr]
                       "evaluability_agreement": ratio(ev_agree, len(items)),
                       "inconclusive_checks": dict(Counter(c.code for r in recs if ok(r) for c in r.checks
                                                           if c.status.value == "INCONCLUSIVE").most_common())}
    att_n = att_ok = 0
    for i in items:
        rec = next((r for r in units[i].records if ok(r)), None)
        if rec is None:
            continue
        for code, want in refs[i].codes.items():
            f = next((f for f in rec.findings if f.code == code), None)
            if f is not None:
                att_n += 1
                att_ok += f.attribution.primary.value == want
        for gate, want in refs[i].gate_attr.items():
            g = rec.gate(gate)
            if g is not None and g.status.value == "FAIL" and g.attribution is not None:
                att_n += 1
                att_ok += g.attribution.primary.value == want
    m["attribution_agreement"] = ratio(att_ok, att_n)
    dw = {i: majority([r.tags.dangerous_win.value if ok(r) and r.tags else "n/a" for r in units[i].records])
          for i in items}
    m["dangerous_win_agreement"] = ratio(sum(dw[i] == refs[i].dangerous_win for i in items), len(items))
    cl = {i: majority([str(r.tags.clean_loss) if ok(r) and r.tags else "n/a" for r in units[i].records])
          for i in items}
    m["clean_loss_agreement"] = ratio(sum(cl[i] == str(refs[i].clean_loss) for i in items), len(items))
    m["pairs"] = pair_results(refs, fr, em)
    m["pair_accuracy"] = ratio(sum(p["both_correct"] for p in m["pairs"].values()), len(m["pairs"]))
    if m["repetitions"] > 1:
        stable_v = sum(len({r.verdict.value if r.verdict else None for r in units[i].records}) == 1 for i in items)
        stable_h = sum(len({r.content_hash() for r in units[i].records}) == 1 for i in items)
        m["consistency"] = {"verdict_stable": ratio(stable_v, len(items)),
                            "record_hash_stable": ratio(stable_h, len(items))}
    else:
        m["consistency"] = {"value": UNMEASURED, "why": "one repetition"}
    lat = [x for i in items for x in units[i].latency]
    m["latency_s"] = {"p50": nearest_rank(lat, 50), "p95": nearest_rank(lat, 95), "n": len(lat)}
    usage: Counter = Counter()
    for i in items:
        usage.update(units[i].usage)
    tokens = {k: usage.get(k, 0) for k in ("llm_calls", "input_tokens", "output_tokens", "cached_tokens",
                                           "schema_retries", "transport_retries")}
    if not tokens["llm_calls"]:
        cost: Any = {"usd": 0.0, "note": "no LLM calls"}
    elif prices is None:
        cost = {"usd": UNMEASURED, "note": "price snapshot pending (B-08); supply --price-in/--price-out"}
    else:
        cost = {"usd": round((tokens["input_tokens"] * prices[0] + tokens["output_tokens"] * prices[1]) / 1e6, 4),
                "note": "estimate from user-supplied USD per million tokens (not the B-08 snapshot)"}
    m["tokens"], m["cost"] = tokens, cost
    return m


def pair_results(refs: dict[str, ItemRef], fr: dict[str, set[str]], em: dict[str, set[str]]) -> dict[str, Any]:
    members: dict[str, dict[str, str]] = {}
    for iid, r in refs.items():
        if r.pair:
            members.setdefault(r.pair[0], {})[r.pair[1]] = iid
    out = {}
    for pid, mm in sorted(members.items()):
        c, v = mm.get("clean"), mm.get("violating")
        if c is None or v is None or c not in fr or v not in fr:
            continue
        target = refs[v].pair_target
        is_gate = target is not None and target in refs[v].gates
        hit = (lambda i: target in fr[i]) if is_gate else (lambda i: target in em[i])
        cv, cc = hit(v), not hit(c)
        out[pid] = {"clean": c, "violating": v, "target": target, "violating_correct": cv, "clean_correct": cc,
                    "both_correct": cv and cc, "inversion": (not cc) and (not cv)}
    return out


# ------------------------------------------------------------------------------------------ per item
def per_item(refs: dict[str, ItemRef], by_system: dict[str, dict[str, Unit]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for iid, ref in refs.items():
        row: dict[str, Any] = {"pack": ref.pack, "intended": {"verdict": ref.verdict, "fired_gates": sorted(ref.fired),
                                                             "codes": sorted(ref.codes),
                                                             "dangerous_win": ref.dangerous_win,
                                                             "clean_loss": ref.clean_loss,
                                                             "label_confidence": ref.label_confidence,
                                                             "ambiguity": ref.ambiguity}, "systems": {}}
        for sys_name, units in by_system.items():
            u = units.get(iid)
            if u is None or not u.records:
                continue
            rec = u.records[0]
            row["systems"][sys_name] = {
                "verdict": verdict_of(u), "record_status": rec.record_status.value,
                "critical_status": rec.critical_status.value if rec.critical_status else None,
                "evaluability": rec.evaluability.status.value if rec.evaluability else None,
                "fired_gates": sorted(majority_set([fired(r) for r in u.records])),
                "codes": sorted(majority_set([emitted(r) for r in u.records])),
                "dangerous_win": rec.tags.dangerous_win.value if ok(rec) and rec.tags else None,
                "clean_loss": rec.tags.clean_loss if ok(rec) and rec.tags else None,
                "unverified_commitments": len(rec.unverified_agent_commitments) if ok(rec) else None,
                "evidence": evidence_rows(rec),
                "disagreements": classify(ref, rec, u),
            }
        out[iid] = row
    return out


def evidence_rows(rec: EvaluationRecord, limit: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not ok(rec):
        return [{"check": "record", "note": rec.failure.reason if rec.failure else "EVALUATION_FAILED"}]
    for g in rec.gates:
        if g.status.value == "FAIL":
            e = g.evidence[0] if g.evidence else None
            rows.append({"check": g.gate.value, "confidence": g.confidence.value if g.confidence else None,
                         "critical_status": g.critical_status.value if g.critical_status else None,
                         "attribution": g.attribution.primary.value if g.attribution else None,
                         "turn": e.turn if e else None, "quote": (e.quote or "")[:90] if e else None})
    for f in rec.findings:
        e = f.evidence[0]
        rows.append({"check": f.code, "severity": f.severity.value, "confidence": f.confidence.value,
                     "state": f.finding_state.value, "attribution": f.attribution.primary.value,
                     "turn": e.turn, "quote": (e.quote or "")[:90]})
    return rows[:limit]


def classify(ref: ItemRef, rec: EvaluationRecord, u: Unit) -> list[dict[str, str]]:
    """Each difference from the design intent, classed as evaluator_failure, benchmark_ambiguity, unsupported_or_oos
    or (for an intended defect that was detected) agent_behavior_error_detected. Labels are never changed."""
    out: list[dict[str, str]] = []
    if not ok(rec):
        return [{"check": "record", "class": "evaluator_failure", "detail": "EVALUATION_FAILED"}]
    fr, em = fired(rec), emitted(rec)
    open_checks = {c.code: c.status.value for c in rec.checks} | {g.gate.value: g.status.value for g in rec.gates
                                                                   if g.status.value in ("INCONCLUSIVE",
                                                                                         "OUT_OF_SCOPE")}
    unverified = {g.gate.value for g in rec.gates if g.evidence_unverified}
    for check in sorted(ref.fired | set(ref.codes)):
        if check in fr or check in em:
            out.append({"check": check, "class": "agent_behavior_error_detected", "detail": "intended defect found"})
        elif check in open_checks:
            out.append({"check": check, "class": "unsupported_or_oos", "detail": f"system: {open_checks[check]}"})
        elif ref.ambiguous:
            out.append({"check": check, "class": "benchmark_ambiguity", "detail": f"missed; design label "
                        f"{ref.label_confidence}, ambiguity: {ref.ambiguity}"})
        else:
            out.append({"check": check, "class": "evaluator_failure", "detail": "intended defect missed"})
    for check in sorted((fr - ref.fired) | (em - set(ref.codes))):
        if check in unverified:
            cls = "unsupported_or_oos"
        elif ref.ambiguous:
            cls = "benchmark_ambiguity"
        else:
            cls = "evaluator_failure"
        out.append({"check": check, "class": cls, "detail": "reported but not intended"})
    if rec.verdict and rec.verdict.value != ref.verdict:
        out.append({"check": "verdict", "class": "evaluator_failure" if not ref.ambiguous else "benchmark_ambiguity",
                    "detail": f"{rec.verdict.value} vs intended {ref.verdict}"})
    return out


def compare_systems(by_system: dict[str, dict[str, Unit]], refs: dict[str, ItemRef]) -> dict[str, Any]:
    """A vs A+ vs B: verdict agreement between systems per item, and the items where A+ changed A's verdict."""
    names = [s for s in SYSTEM_ORDER if s in by_system]
    out: dict[str, Any] = {"systems": names, "pairwise_verdict_agreement": {}}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            common = [iid for iid in refs if by_system[a].get(iid) and by_system[b].get(iid)
                      and by_system[a][iid].records and by_system[b][iid].records]
            same = sum(verdict_of(by_system[a][x]) == verdict_of(by_system[b][x]) for x in common)
            out["pairwise_verdict_agreement"][f"{a} vs {b}"] = ratio(same, len(common))
    if "A" in by_system and "A+" in by_system:
        out["aplus_changed_verdict"] = sorted(
            iid for iid in refs if by_system["A"].get(iid) and by_system["A+"].get(iid)
            and verdict_of(by_system["A"][iid]) != verdict_of(by_system["A+"][iid]))
    return out


__all__ = ["FIRED", "ItemRef", "UNMEASURED", "Unit", "build_reference", "classify", "compare_systems", "load_run",
           "pair_results", "per_item", "system_metrics"]
