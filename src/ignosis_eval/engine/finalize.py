"""Shared deterministic finalization — rubric.yaml › architecture_application.A_PLUS (AJ-06): front-end merge,
then 1 capability filter, 2 external-truth filter, 3 evidence verifier, 4 confidence cap, 5 status re-map,
6 attribution, 7 repair allowlist, 8 verdict (V2–V7, PARTIAL) and tags.

Used by A+ (on A's stored raw output, no LLM call), by B (after its rule engine) and by K0. Every
change relative to the input body is written to the derivation log (P-11 derivation_log.json for A+).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.capabilities import perception_allowed
from ignosis_eval.contracts.enums import (
    ActionType,
    Attribution,
    CheckStatus,
    Confidence,
    ConfidenceSource,
    CriticalStatus,
    EvaluabilityStatus,
    GateId,
    GateStatus,
    ModeApplicability,
    OosReason,
    RecordStatus,
    Role,
)
from ignosis_eval.contracts.evaluation_record import (
    CheckStatusEntry,
    EvaluabilityResult,
    EvaluationRecord,
    Evidence,
    Finding,
    GateResult,
    OutOfScopeEntry,
    RecordBody,
    SystemInfo,
)
from ignosis_eval.contracts.evidence import quote_score
from ignosis_eval.engine import confidence as conf
from ignosis_eval.engine.attribution import attribute
from ignosis_eval.engine.measure import trt06_measure
from ignosis_eval.engine.verdict import apply_repair, decide, routing_tier, tags
from ignosis_eval.pipeline.lexicon import scan
from ignosis_eval.spec.loader import Spec


@dataclass
class DerivationLog:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, step: str, target: str, change: str, **detail: Any) -> None:
        self.entries.append({"step": step, "target": target, "change": change, **detail})


@dataclass
class Facts:
    """Deterministic facts available to the engine (from B's extraction; empty for A+)."""

    det_confirmed: dict[str, bool] = field(default_factory=dict)  # check id -> deterministic confirmation
    registered: dict[str, bool] = field(default_factory=dict)  # check id -> responds_to link exists
    material_difference: dict[str, bool] = field(default_factory=dict)  # check id -> SAID != HEARD
    readback_safeguard: dict[str, bool] = field(default_factory=dict)


def _reference_text(ev: Evidence, ni: NormalizedInput) -> str | None:
    t = ni.turn_by_number(ev.turn) if ev.turn else None
    if t is None:
        return None
    if ev.source == "supplied":
        return t.supplied_text
    if ev.source == "asr":
        return t.asr_text
    return t.text


def verify_evidence(ev: Evidence, ni: NormalizedInput, threshold: float) -> tuple[bool, str | None]:
    """SD-13 faithfulness: turn exists, role matches, score >= quote_match_min. Header evidence is structural."""
    if ev.header_field is not None:
        return (ev.header_field == "call_start_ts" and ni.header.call_start_ts is not None), "header"
    t = ni.turn_by_number(ev.turn) if ev.turn else None
    if t is None:
        return False, "unknown turn"
    if ev.role is not t.role:
        return False, "role mismatch"
    ref = _reference_text(ev, ni)
    if ref is None:
        return False, f"no {ev.source} text for turn"
    if quote_score(ev.quote or "", ref) < threshold:
        return False, "quote below quote_match_min"
    return True, None


def _cited_turns(evs: list[Evidence], ni: NormalizedInput):
    return [ni.turn_by_number(e.turn) for e in evs if e.turn and ni.turn_by_number(e.turn)]


def _lexicon_hit(check_id: str, sub_rule: str | None, evs: list[Evidence], ni: NormalizedInput, spec: Spec) -> bool:
    """Prohibited-consequence lexicon hit without negation in a cited AGENT turn (terms only; B-04)."""
    gate = {"G3": "G3", "G2": "G2"}.get(check_id)
    if gate is None or (check_id == "G2" and sub_rule not in (None, "G2a")):
        return False
    cats = {c: spec.lexicon_terms("prohibited_consequences", c)
            for c, body in spec.profile["lexicons"]["prohibited_consequences"].items() if body["maps_to_gate"] == gate}
    if not any(cats.values()):
        return False
    negation = spec.lexicon_terms("negation_tokens")
    window = int(spec.threshold("negation_window_tokens"))
    for t in _cited_turns(evs, ni):
        if t.role is Role.AGENT and any(not h.negated for h in scan(t.text, cats, negation, window)):
            return True
    return False


def _template_description(cd_raw: dict, code: str) -> str:
    """§9.7 / AJ-06 external-truth filter: A+ free text is replaced by rubric templates, which cannot express
    ledger, payment, record or authority truth."""
    return f"{code}: {cd_raw.get('name', code)}"


def finalize(body: RecordBody, ni: NormalizedInput, spec: Spec, *, system: SystemInfo, facts: Facts | None = None,
             log: DerivationLog | None = None, rejudge=None,
             confidence_source: ConfidenceSource = ConfidenceSource.COMPUTED) -> EvaluationRecord:
    """Shared deterministic post-processing (rubric.yaml › architecture_application.A_PLUS, AJ-06), in order:
    (0) front-end merge (V1 pre-checks; missing gates INCONCLUSIVE), then
    1 capability filter · 2 external-truth filter · 3 evidence verifier · 4 confidence cap · 5 status re-map ·
    6 attribution · 7 repair allowlist · 8 verdict and tags. Every change is logged under its step name."""
    reg = spec.registry
    facts = facts or Facts()
    log = log or DerivationLog()
    threshold = float(spec.threshold("quote_match_min"))
    prov = None if ni.unit_mode.value == "A" else ni.header.transcript_provenance
    pre_ids = {p.gate.value for p in ni.frontend.prechecks}

    def mode_oos(cid: str) -> OosReason | None:
        app, reason = reg.mode_status(cid, ni.input_mode, has_call_start_ts=ni.header.call_start_ts is not None,
                                      has_timestamps=ni.has_timestamps, provenance=prov)
        return reason if app is ModeApplicability.OUT_OF_SCOPE else None

    # ---------------------------------------------------------------- 0 front-end merge
    by_gate: dict[str, GateResult] = {g.gate.value: g for g in body.gates}
    for pre in ni.frontend.prechecks:  # V1: deterministic pre-checks are authoritative
        prev = by_gate.get(pre.gate.value)
        if prev is None or prev.status != pre.status:
            log.add("0-frontend-merge", pre.gate.value, "pre-check result", before=prev.status.value if prev else None,
                    after=pre.status.value)
        by_gate[pre.gate.value] = pre
    for gid in reg.gate_ids:
        if gid not in by_gate:
            log.add("0-frontend-merge", gid, "missing gate set INCONCLUSIVE (never PASS)")
            by_gate[gid] = GateResult(gate=GateId(gid), status=GateStatus.INCONCLUSIVE)
    raw_findings = list(body.findings) + list(ni.frontend.precheck_findings)

    # ---------------------------------------------------------------- 1 capability filter
    for gid, g in list(by_gate.items()):
        oos = mode_oos(gid)
        if oos is not None and g.status is not GateStatus.OUT_OF_SCOPE and gid not in pre_ids:
            log.add("1-capability-filter", gid, "set OUT_OF_SCOPE", before=g.status.value, reason=oos.value)
            by_gate[gid] = GateResult(gate=g.gate, status=GateStatus.OUT_OF_SCOPE, oos_reason=oos)
    kept: list[Finding] = []
    for f in raw_findings:
        if f.code in reg.checks and mode_oos(f.code) is not None:
            log.add("1-capability-filter", f.code, "dropped finding: OUT_OF_SCOPE in this mode",
                    reason=mode_oos(f.code).value)  # type: ignore[union-attr]
            continue
        kept.append(f)
    checks: dict[str, CheckStatusEntry] = {}
    for c in body.checks:
        checks[c.code] = c
    for cid in reg.mvp_code_ids:
        oos = mode_oos(cid)
        if oos is not None:
            checks[cid] = CheckStatusEntry(code=cid, status=CheckStatus.OUT_OF_SCOPE, oos_reason=oos)

    # ---------------------------------------------------------------- 2 external-truth filter
    ext: list[Finding] = []
    for f in kept:
        if f.code in reg.oos_codes:
            log.add("2-external-truth-filter", f.code, "dropped finding on an always-OUT_OF_SCOPE code")
            continue
        if f.code not in reg.checks:
            log.add("2-external-truth-filter", f.code, "dropped unknown code")
            continue
        templ = _template_description(reg.get(f.code).raw, f.code)
        if f.description != templ:
            f = f.model_copy(update={"description": templ})
        ext.append(f)
    for code in [c for c in checks if c in reg.oos_codes or c not in reg.checks]:
        log.add("2-external-truth-filter", code, "dropped check entry on an always-OUT_OF_SCOPE / unknown code")
        checks.pop(code)
    outcome = body.outcome
    if outcome is not None and outcome.verified is not None:
        log.add("2-external-truth-filter", "outcome.verified", "set null (verified outcomes are OUT_OF_SCOPE)")
        outcome = outcome.model_copy(update={"verified": None})
    for gid, g in list(by_gate.items()):
        if gid not in pre_ids and g.note:
            by_gate[gid] = g.model_copy(update={"note": None})
    out_of_scope = [OutOfScopeEntry(code=c, oos_reason=OosReason.EXTERNAL_DATA_REQUIRED) for c in reg.oos_codes]

    # ---------------------------------------------------------------- 3 evidence verifier
    quote_ok: dict[str, bool] = {}
    unverified: dict[str, bool] = {}
    for gid, g in list(by_gate.items()):
        if gid in pre_ids or g.status not in (GateStatus.FAIL, GateStatus.INCONCLUSIVE):
            continue
        ev_checks = [verify_evidence(e, ni, threshold) for e in g.evidence]
        ok = bool(ev_checks) and all(v for v, _ in ev_checks)
        if not ok and rejudge is not None:  # §9.6: a gate citation that fails verification is re-judged once
            g = rejudge(g) or g
            ok = bool(g.evidence) and all(verify_evidence(e, ni, threshold)[0] for e in g.evidence)
            by_gate[gid] = g
        quote_ok[gid] = ok
        unverified[gid] = g.evidence_unverified or (not ok and g.status is GateStatus.FAIL)
        if not ok and g.status is GateStatus.FAIL:
            log.add("3-evidence-verifier", gid, "citation unverifiable -> SUSPECTED, evidence_unverified=true")
    verified: list[Finding] = []
    for f in ext:
        results = [verify_evidence(e, ni, threshold) for e in f.evidence]
        if not all(v for v, _ in results):
            log.add("3-evidence-verifier", f.code, "dropped non-gate finding with unverifiable citation",
                    reasons=[r for v, r in results if not v])
            continue
        if f.code == "TRT-06":  # DET (AJ-02): basis from timestamps, threshold re-checked on the cited turn
            turn = ni.turn_by_number(f.evidence[0].turn or 0)
            basis, exceeds = trt06_measure(turn, ni, spec) if turn is not None else (None, False)
            if not exceeds:
                log.add("3-evidence-verifier", f.code, "dropped: cited turn does not exceed the monologue threshold",
                        basis=basis.value if basis else None)
                continue
            f = f.model_copy(update={"measurement_basis": basis})
            facts.det_confirmed.setdefault("TRT-06", True)
        verified.append(f)

    # ---------------------------------------------------------------- 4 confidence cap
    gate_level: dict[str, Confidence] = {}
    for gid, g in by_gate.items():
        if gid in pre_ids or gid not in quote_ok:
            continue
        cd = reg.get(gid)
        turns = _cited_turns(g.evidence, ni)
        gate_level[gid] = conf.compute(
            cd, sub_rule=g.sub_rule, quote_ok=quote_ok[gid], role_ok=quote_ok[gid],
            spans_reliable=not any(t.unreliable for t in turns), det_confirmed=facts.det_confirmed.get(gid, False),
            lexicon_hit_without_negation=_lexicon_hit(gid, g.sub_rule, g.evidence, ni, spec), llm_label=g.confidence)
        if gate_level[gid] is not g.confidence:
            log.add("4-confidence-cap", gid, "computed", before=g.confidence and g.confidence.value,
                    after=gate_level[gid].value)
    finding_level: list[Confidence] = []
    for f in verified:
        turns = _cited_turns(f.evidence, ni)
        finding_level.append(conf.compute(
            reg.get(f.code), sub_rule=f.sub_rule, quote_ok=True, role_ok=True,
            spans_reliable=not any(t.unreliable for t in turns), det_confirmed=facts.det_confirmed.get(f.code, False),
            lexicon_hit_without_negation=False, llm_label=f.confidence))

    # ---------------------------------------------------------------- 5 status re-map
    for gid, level in gate_level.items():
        g = by_gate[gid]
        status, cs = conf.route_gate(g.status, level, g.in_span_trigger)
        if unverified.get(gid) and status is GateStatus.FAIL:
            cs = CriticalStatus.SUSPECTED
        if (status, cs, level) != (g.status, g.critical_status, g.confidence):
            log.add("5-status-remap", gid, "recomputed", before=[g.status.value, g.confidence and g.confidence.value],
                    after=[status.value, level.value, cs.value if cs else None])
        by_gate[gid] = g.model_copy(update={"status": status, "critical_status": cs, "confidence": level,
                                            "evidence_unverified": bool(unverified.get(gid))})
    for gid, g in list(by_gate.items()):
        if gid not in pre_ids and g.status is not GateStatus.FAIL and g.critical_status is not None:
            by_gate[gid] = g.model_copy(update={"critical_status": None})
    remapped = [f.model_copy(update={"confidence": lv, "finding_state": conf.finding_state(f.severity, lv)})
                for f, lv in zip(verified, finding_level, strict=True)]

    # ---------------------------------------------------------------- 6 attribution
    attributed: list[Finding] = []
    for gid, g in list(by_gate.items()):
        if gid in pre_ids or g.status not in (GateStatus.FAIL, GateStatus.INCONCLUSIVE):
            continue
        turns = _cited_turns(g.evidence, ni)
        by_gate[gid] = g.model_copy(update={"attribution": attribute(
            reg.get(gid), input_mode=ni.input_mode, provenance=prov, registered=facts.registered.get(gid),
            material_difference=facts.material_difference.get(gid), readback_safeguard=facts.readback_safeguard.get(gid),
            own_span_confidence_low=any(t.unreliable for t in turns))})
    for f in remapped:
        cd = reg.get(f.code)
        turns = _cited_turns(f.evidence, ni)
        attr = attribute(cd, input_mode=ni.input_mode, provenance=prov, registered=facts.registered.get(f.code),
                         material_difference=facts.material_difference.get(f.code),
                         readback_safeguard=facts.readback_safeguard.get(f.code),
                         own_span_confidence_low=any(t.unreliable for t in turns))
        if attr.primary is Attribution.PERCEPTION and not perception_allowed(ni.input_mode, prov):
            attr = attr.model_copy(update={"primary": Attribution.INDETERMINATE})
        action = f.action_type if (cd.action_type_rule and f.action_type in (ActionType.REMEDIATE, ActionType.FIX)) \
            else (cd.action_types[0] if cd.action_types else f.action_type)
        if attr != f.attribution:
            log.add("6-attribution", f.code, "recomputed", attribution=attr.primary.value)
        attributed.append(f.model_copy(update={"attribution": attr, "action_type": action}))

    # ---------------------------------------------------------------- 7 repair allowlist
    findings: list[Finding] = []
    for f in attributed:
        nf = apply_repair(f, reg)
        if (nf.repair_status, nf.severity) != (f.repair_status, f.severity):
            log.add("7-repair-allowlist", f.code, "repair flag ignored (not on the allowlist)" if f.code not in
                    reg.repair_allowlist else "ACC-05 severity from repair status",
                    before=[f.repair_status.value, f.severity.value], after=[nf.repair_status.value, nf.severity.value])
        nf = nf.model_copy(update={"finding_state": conf.finding_state(nf.severity, nf.confidence)})
        findings.append(nf)
    for f in findings:
        checks.pop(f.code, None)

    # ---------------------------------------------------------------- 8 verdict and tags
    gates = [by_gate[gid] for gid in reg.gate_ids]
    fe = ni.frontend
    vr = decide(evaluability=fe.evaluability_status, reason_codes=list(fe.reason_codes), gates=gates,
                findings=findings, checks=list(checks.values()), registry=reg)
    tg = tags(verdict=vr.verdict, gates=vr.gates, findings=vr.findings, outcome=outcome, registry=reg)
    tier = routing_tier(verdict=vr.verdict, critical_status=vr.critical_status, tags_=tg, findings=vr.findings)
    if body.verdict is not None and body.verdict is not vr.verdict:
        log.add("8-verdict-and-tags", "call", "recomputed by verdict engine", before=body.verdict.value,
                after=vr.verdict.value)
    reasons = list(fe.reason_codes)
    if vr.evaluability is EvaluabilityStatus.PARTIAL:
        for rc in [r for g in vr.gates if g.status is GateStatus.INCONCLUSIVE for r in g.reason_codes] + \
                  [r for c in vr.checks if c.status is CheckStatus.INCONCLUSIVE for r in c.reason_codes]:
            if rc not in reasons:
                reasons.append(rc)
    return EvaluationRecord(
        record_status=RecordStatus.OK, confidence_source=confidence_source, system=system,
        contract_version=spec.contract_version, rubric_version=spec.rubric_version, profile_id=spec.profile_id,
        profile_version=spec.profile_version, input_mode=ni.input_mode, unit_mode=ni.unit_mode, verdict=vr.verdict,
        critical_status=vr.critical_status, within_scope_complete=vr.within_scope_complete,
        evaluability=EvaluabilityResult(status=vr.evaluability, reason_codes=reasons),
        gates=vr.gates, findings=vr.findings, checks=vr.checks, dimensions=dict(body.dimensions),
        outcome=outcome, tags=tg, routing_tier=tier, out_of_scope=out_of_scope,
        unverified_agent_commitments=list(body.unverified_agent_commitments))


__all__ = ["DerivationLog", "Facts", "finalize", "verify_evidence"]
