"""Shared deterministic finalization: evidence verifier -> capability enforcement -> pre-checks (V1) ->
computed confidence + routing -> attribution rules -> repair -> always-OOS list -> verdict engine (V2–V7)
-> tags -> routing tier.

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
    CriticalStatus,
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


def finalize(body: RecordBody, ni: NormalizedInput, spec: Spec, *, system: SystemInfo, facts: Facts | None = None,
             log: DerivationLog | None = None, rejudge=None) -> EvaluationRecord:
    reg = spec.registry
    facts = facts or Facts()
    log = log or DerivationLog()
    threshold = float(spec.threshold("quote_match_min"))
    role_min = float(spec.threshold("role_confidence_min"))
    prov = None if ni.unit_mode.value == "A" else ni.header.transcript_provenance

    def mode_oos(cid: str) -> OosReason | None:
        app, reason = reg.mode_status(cid, ni.input_mode, has_call_start_ts=ni.header.call_start_ts is not None,
                                      has_timestamps=ni.has_timestamps, provenance=prov)
        return reason if app is ModeApplicability.OUT_OF_SCOPE else None

    # ---------------------------------------------------------------- gates: verify, capability, pre-checks
    by_gate: dict[str, GateResult] = {g.gate.value: g for g in body.gates}
    for pre in ni.frontend.prechecks:  # V1: deterministic pre-checks are authoritative
        prev = by_gate.get(pre.gate.value)
        if prev is None or prev.status != pre.status:
            log.add("precheck", pre.gate.value, "set from front end", before=prev.status.value if prev else None,
                    after=pre.status.value)
        by_gate[pre.gate.value] = pre
    gates: list[GateResult] = []
    for gid in reg.gate_ids:
        g = by_gate.get(gid)
        if g is None:
            log.add("gates", gid, "missing gate set INCONCLUSIVE (never PASS)")
            g = GateResult(gate=gid, status=GateStatus.INCONCLUSIVE)
        is_pre = any(p.gate.value == gid for p in ni.frontend.prechecks)
        oos = mode_oos(gid)
        if oos is not None and g.status is not GateStatus.OUT_OF_SCOPE and not is_pre:
            log.add("capability", gid, "set OUT_OF_SCOPE", before=g.status.value, reason=oos.value)
            g = GateResult(gate=g.gate, status=GateStatus.OUT_OF_SCOPE, oos_reason=oos)
        if not is_pre and g.status in (GateStatus.FAIL, GateStatus.INCONCLUSIVE):
            cd = reg.get(gid)
            ev_checks = [verify_evidence(e, ni, threshold) for e in g.evidence]
            quote_ok = bool(ev_checks) and all(ok for ok, _ in ev_checks)
            unverified = g.evidence_unverified
            if not quote_ok:
                if rejudge is not None:  # §9.6: a gate citation that fails verification is re-judged once
                    g = rejudge(g) or g
                    quote_ok = bool(g.evidence) and all(verify_evidence(e, ni, threshold)[0] for e in g.evidence)
                if not quote_ok and g.status is GateStatus.FAIL:
                    unverified = True
                    log.add("verifier", gid, "citation unverifiable -> SUSPECTED, evidence_unverified=true")
            turns = _cited_turns(g.evidence, ni)
            level = conf.compute(
                cd, sub_rule=g.sub_rule, quote_ok=quote_ok, role_ok=quote_ok,
                spans_reliable=not any(t.unreliable for t in turns), det_confirmed=facts.det_confirmed.get(gid, False),
                lexicon_hit_without_negation=_lexicon_hit(gid, g.sub_rule, g.evidence, ni, spec),
                role_confidence_low=any(t.role_confidence < role_min for t in turns), llm_label=g.confidence)
            status, cs = conf.route_gate(g.status, level, g.in_span_trigger)
            if unverified and status is GateStatus.FAIL:
                cs = CriticalStatus.SUSPECTED
            attr = attribute(cd, input_mode=ni.input_mode, provenance=prov, registered=facts.registered.get(gid),
                             material_difference=facts.material_difference.get(gid),
                             readback_safeguard=facts.readback_safeguard.get(gid),
                             own_span_confidence_low=any(t.unreliable for t in turns))
            if (status, cs, level) != (g.status, g.critical_status, g.confidence):
                log.add("confidence", gid, "recomputed", before=[g.status.value, g.confidence and g.confidence.value],
                        after=[status.value, level.value, cs.value if cs else None])
            g = g.model_copy(update={"status": status, "critical_status": cs, "confidence": level,
                                     "attribution": attr, "evidence_unverified": unverified})
        elif not is_pre and g.status is not GateStatus.FAIL and g.critical_status is not None:
            g = g.model_copy(update={"critical_status": None})
        gates.append(g)

    # ---------------------------------------------------------------- findings
    findings: list[Finding] = []
    for f in list(body.findings) + list(ni.frontend.precheck_findings):
        if f.code in reg.oos_codes:
            log.add("oos", f.code, "dropped finding on an always-OUT_OF_SCOPE code")
            continue
        if f.code not in reg.checks:
            log.add("schema", f.code, "dropped unknown code")
            continue
        cd = reg.get(f.code)
        oos = mode_oos(f.code)
        if oos is not None:
            log.add("capability", f.code, "dropped finding: OUT_OF_SCOPE in this mode", reason=oos.value)
            continue
        verdicts = [verify_evidence(e, ni, threshold) for e in f.evidence]
        if not all(ok for ok, _ in verdicts):
            log.add("verifier", f.code, "dropped non-gate finding with unverifiable citation",
                    reasons=[r for ok, r in verdicts if not ok])
            continue
        turns = _cited_turns(f.evidence, ni)
        level = conf.compute(
            cd, sub_rule=f.sub_rule, quote_ok=True, role_ok=True, spans_reliable=not any(t.unreliable for t in turns),
            det_confirmed=facts.det_confirmed.get(f.code, False),
            lexicon_hit_without_negation=False, role_confidence_low=any(t.role_confidence < role_min for t in turns),
            llm_label=f.confidence)
        attr = attribute(cd, input_mode=ni.input_mode, provenance=prov, registered=facts.registered.get(f.code),
                         material_difference=facts.material_difference.get(f.code),
                         readback_safeguard=facts.readback_safeguard.get(f.code),
                         own_span_confidence_low=any(t.unreliable for t in turns))
        if attr.primary is Attribution.PERCEPTION and not perception_allowed(ni.input_mode, prov):
            attr = attr.model_copy(update={"primary": Attribution.INDETERMINATE})
        action = f.action_type if (cd.action_type_rule and f.action_type in (ActionType.REMEDIATE, ActionType.FIX)) \
            else (cd.action_types[0] if cd.action_types else f.action_type)
        nf = f.model_copy(update={"confidence": level, "attribution": attr, "action_type": action})
        nf = apply_repair(nf, reg)
        nf = nf.model_copy(update={"finding_state": conf.finding_state(nf.severity, level)})
        if (nf.confidence, nf.finding_state, nf.severity, nf.attribution) != (f.confidence, f.finding_state,
                                                                             f.severity, f.attribution):
            log.add("finding", f.code, "recomputed", confidence=level.value, state=nf.finding_state.value,
                    severity=nf.severity.value, attribution=attr.primary.value)
        findings.append(nf)

    # ---------------------------------------------------------------- explicit check statuses + always-OOS
    checks: dict[str, CheckStatusEntry] = {}
    for c in body.checks:
        if c.code in reg.oos_codes or c.code not in reg.checks:
            continue
        checks[c.code] = c
    for cid in reg.mvp_code_ids:
        oos = mode_oos(cid)
        if oos is not None:
            checks[cid] = CheckStatusEntry(code=cid, status=CheckStatus.OUT_OF_SCOPE, oos_reason=oos)
    for f in findings:
        checks.pop(f.code, None)
    out_of_scope = [OutOfScopeEntry(code=c, oos_reason=OosReason.EXTERNAL_DATA_REQUIRED) for c in reg.oos_codes]

    # ---------------------------------------------------------------- verdict, tags, routing
    fe = ni.frontend
    if body.evaluability and body.evaluability.status.value != fe.evaluability_status.value:
        log.add("evaluability", "call", "front-end evaluability used", system_claim=body.evaluability.status.value,
                front_end=fe.evaluability_status.value)
    vr = decide(evaluability=fe.evaluability_status, reason_codes=list(fe.reason_codes), gates=gates,
                findings=findings, checks=list(checks.values()), registry=reg)
    tg = tags(verdict=vr.verdict, gates=vr.gates, findings=vr.findings, outcome=body.outcome, registry=reg)
    tier = routing_tier(verdict=vr.verdict, critical_status=vr.critical_status, tags_=tg, findings=vr.findings)
    if body.verdict is not None and body.verdict is not vr.verdict:
        log.add("verdict", "call", "recomputed by verdict engine", before=body.verdict.value, after=vr.verdict.value)
    return EvaluationRecord(
        record_status=RecordStatus.OK, system=system, contract_version=spec.contract_version,
        rubric_version=spec.rubric_version, profile_id=spec.profile_id, profile_version=spec.profile_version,
        input_mode=ni.input_mode, unit_mode=ni.unit_mode, verdict=vr.verdict, critical_status=vr.critical_status,
        within_scope_complete=vr.within_scope_complete,
        evaluability=EvaluabilityResult(status=fe.evaluability_status, reason_codes=list(fe.reason_codes)),
        gates=vr.gates, findings=vr.findings, checks=vr.checks, dimensions=dict(body.dimensions),
        outcome=body.outcome, tags=tg, routing_tier=tier, out_of_scope=out_of_scope,
        unverified_agent_commitments=list(body.unverified_agent_commitments))


__all__ = ["DerivationLog", "Facts", "finalize", "verify_evidence"]
