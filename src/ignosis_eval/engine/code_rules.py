"""Evaluator B's rule engine over the verified extraction (rubric.yaml 1.2-mvp): every MVP gate and code, the
targeted judgments (LLM call #2) the rubric assigns to LLM_JUDGE checks, and the observable outcome.

`plan(ex, ni, spec)` runs every deterministic rule (engine/rules.py for G1, G2/G3 categories, G2b, G4, G5/RES-06,
ACC-03u, ACC-05, TRT-06, plus the rules below) and returns a `Plan`: the deterministic output and one `Pending`
item per judgment the rubric requires (rubric.yaml › judgments; each has the trigger stated there). `resolve(plan,
answers)` applies the judgment answers (a missing, invalid or uncited answer counts as CANNOT_DETERMINE: "check
INCONCLUSIVE; for gates with in-span trigger -> SUSPECTED") and assembles the record body. The deterministic
finalization (engine/finalize.py) then applies the capability filter, evidence verification, confidence caps,
attribution, repair and the verdict.

Never PASS by default: a check whose rule cannot run is INCONCLUSIVE with the reason in `Plan.notes`:
  * RES-11 needs `loop_similarity`, which is PENDING_HUMAN_SIGNOFF (B-11);
  * UND-12 needs the slot an ask targets, which extraction 2.0.0 does not carry (INCONCLUSIVE only when an ask
    follows a stated commitment, i.e. when a re-ask is possible);
  * UND-03 / COM-05 compare normalized values; read-back values and constraint dates arrive as raw text, and number
    words need the numerals lexicon, whose reviewed `terms` are empty until B-04. Digits are compared; anything else
    is INCONCLUSIVE (convention, docs/spec-reconciliation.md §3.46);
  * PLT-02 (overlap_min_seconds PENDING) and PLT-03 / PLT-04 (SAID vs HEARD comparison not built) where in scope.
The conventions for the remaining open points (ACC-04 vs UND-04, COM-03 on callbacks, TRT-03 language
compatibility, TRT-01 strength, outcome dispositions) are listed in docs/spec-reconciliation.md §3.46.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import (
    ActionType,
    CheckStatus,
    Confidence,
    CriticalStatus,
    Disposition,
    Firmness,
    GateId,
    GateStatus,
    InputMode,
    OutcomeAttribution,
    ReasonCode,
    Role,
    Severity,
    TranscriptProvenance,
)
from ignosis_eval.contracts.evaluation_record import (
    CheckStatusEntry,
    Evidence,
    Finding,
    GateResult,
    Outcome,
    RecordBody,
)
from ignosis_eval.contracts.extraction import Commitment, ExtractedEvent, ExtractionOutput
from ignosis_eval.engine.finalize import Facts
from ignosis_eval.engine.rules import (
    _PLACEHOLDER_ATTR,
    RuleOutput,
    apply_implemented,
    commitment_turn,
    terminal_trigger,
)
from ignosis_eval.spec.loader import PENDING, Spec

CANNOT = "CANNOT_DETERMINE"
G6_EXPLICIT = ("EXPLICIT_SELF_HARM", "MEDICAL_EMERGENCY", "DEATH_REPORTED")
G6_CARE_ROUTES = ("care_protocol", "human_transfer", "callback")
RES04_ROUTES = ("escalation", "human_transfer", "callback")  # RES-04: route in {escalation, human_transfer, callback}
COM06_ROUTES = ("callback", "dispute", "escalation", "verification", "hardship")
UND01_TRIGGERS = ("payment_claim", "dispute_amount", "dispute_liability")
RES01_TRIGGERS = ("dispute_amount", "dispute_liability", "payment_claim", "inability", "hardship_reason")
RES01_REMEDIATE = ("dispute_amount", "dispute_liability", "payment_claim")
RES02_EXCLUDE = ("dispute_amount", "dispute_liability", "payment_claim", "inability", "hardship_reason", "refusal",
                 "stop_request", "human_request", "vulnerability_cue", "third_party_signal")
GATE_RANK = {"NA": 0, "PASS": 1, "INCONCLUSIVE": 2, "INCONCLUSIVE+trigger": 3, "FAIL": 4}
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), start=1)}


# ------------------------------------------------------------------------------------------ plan / pending
@dataclass
class Pending:
    """One judgment request (rubric.yaml › judgments) and how its answer is applied."""

    judgment: str
    target: str
    turns: list[int]
    resolve: Callable[[str], RuleOutput]  # answer (CANNOT_DETERMINE when missing / invalid) -> output


@dataclass
class Plan:
    det: RuleOutput
    pending: list[Pending] = field(default_factory=list)
    checks: dict[str, CheckStatusEntry] = field(default_factory=dict)  # deterministic INCONCLUSIVE / NA entries
    notes: list[dict] = field(default_factory=list)
    firmness: dict[str, str] = field(default_factory=dict)  # commitment id -> J-FIRM answer (lower case)
    ex: ExtractionOutput | None = None
    ni: NormalizedInput | None = None


def _gate_key(g: GateResult) -> int:
    if g.status is GateStatus.INCONCLUSIVE and g.in_span_trigger:
        return GATE_RANK["INCONCLUSIVE+trigger"]
    return GATE_RANK.get(g.status.value, 0)


def merge_gate(a: GateResult | None, b: GateResult) -> GateResult:
    """Worst result wins: FAIL > INCONCLUSIVE with in-span trigger > INCONCLUSIVE > PASS > NA."""
    if a is None or _gate_key(b) > _gate_key(a):
        return b
    return a


# ------------------------------------------------------------------------------------------ helpers
def _agent_turns(ni: NormalizedInput) -> list[int]:
    return [t.turn for t in ni.turns if t.role is Role.AGENT]


def _next_agent(ni: NormalizedInput, turn: int, k: int = 1) -> list[int]:
    return [t for t in _agent_turns(ni) if t > turn][:k]


def _ev(e: ExtractedEvent, element: str | None = None) -> Evidence:
    return Evidence(turn=e.turn, quote=e.quote, role=e.side, source=e.source, element=element)


def _turn_ev(ni: NormalizedInput, turn: int | None, element: str | None = None) -> Evidence | None:
    t = ni.turn_by_number(turn) if turn else None
    return Evidence(turn=t.turn, quote=t.text, role=t.role, element=element) if t is not None else None


def _evs(*items: Evidence | None) -> list[Evidence]:
    return [e for e in items if e is not None]


def _responders(ex: ExtractionOutput, event_id: str) -> list[ExtractedEvent]:
    return sorted((e for e in ex.events if e.side is Role.AGENT and event_id in e.responds_to), key=lambda e: e.turn)


def _finding(code: str, spec: Spec, evidence: list[Evidence], anchor: int | None, confidence: Confidence, *,
             severity: Severity | None = None, action: ActionType | None = None, sub_rule: str | None = None
             ) -> Finding:
    cd = spec.registry.get(code)
    sev = severity or cd.severity or Severity.MAJOR
    act = action or (cd.action_types[0] if cd.action_types else ActionType.FIX)
    return Finding(code=code, sub_rule=sub_rule, severity=sev, confidence=confidence, action_type=act,
                   attribution=_PLACEHOLDER_ATTR, evidence=evidence, anchor_turn=anchor)


def _inconclusive(plan: Plan, code: str, why: str, *reasons: ReasonCode) -> None:
    if code not in plan.checks:
        plan.checks[code] = CheckStatusEntry(code=code, status=CheckStatus.INCONCLUSIVE, reason_codes=list(reasons))
        plan.notes.append({"check": code, "status": "INCONCLUSIVE", "why": why})


def _check_out(code: str, why: str, *reasons: ReasonCode) -> RuleOutput:
    """A RuleOutput carrying one INCONCLUSIVE check entry (used by judgment resolutions)."""
    out = RuleOutput()
    out.trace.append({"check": code, "status": "INCONCLUSIVE", "why": why,
                      "reason_codes": [r.value for r in reasons]})
    return out


def _terminal(plan: Plan, code: str, turn: int, ni: NormalizedInput, spec: Spec) -> bool:
    """SC-01 for non_response checks: no agent turn after the trigger -> INCONCLUSIVE (never DEFECT / PASS)."""
    res = terminal_trigger(code, turn, ni, spec)
    if res is None:
        return False
    if isinstance(res, GateResult):
        plan.det.gates[code] = merge_gate(plan.det.gates.get(code), res)
    else:
        _inconclusive(plan, code, f"no agent turn after the trigger at turn {turn} (SC-01)",
                      ReasonCode.NO_AGENT_TURN_AFTER_TRIGGER)
    return True


# ------------------------------------------------------------------------------------------ value parsing
_AMOUNT = re.compile(r"(?<![\d,])(\d{1,3}(?:,\d{2,3})+|\d{3,})(?![\d,])")
_DAY = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?(?!\d)\s*(?:tareekh|tarikh|taarikh|tarik)?", re.IGNORECASE)
_MONTH = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedValues:
    amounts: tuple[int, ...]
    days: tuple[int, ...]
    months: tuple[int, ...]


def parse_values(raw: str | None) -> ParsedValues:
    """Digit amounts (>= 3 digits or grouped) and day-of-month digits (1–31) with English month names. Number words
    are not read: the numerals lexicon is empty until B-04 (profile `terms` only; seeds are never used)."""
    if not raw:
        return ParsedValues((), (), ())
    amounts = tuple(int(m.replace(",", "")) for m in _AMOUNT.findall(raw))
    rest = _AMOUNT.sub(" ", raw)
    days = tuple(int(d) for d in _DAY.findall(rest) if 1 <= int(d) <= 31)
    months = tuple(MONTHS[m.lower()[:3]] for m in _MONTH.findall(raw))
    return ParsedValues(amounts, days, months)


def _date_parts(d: date | None) -> tuple[int, int] | None:
    return (d.day, d.month) if d is not None else None


# ------------------------------------------------------------------------------------------ J-REG codes
def _jreg(plan: Plan, code: str, trigger: ExtractedEvent, ni: NormalizedInput, spec: Spec, element: str) -> bool:
    """UND-01 / UND-02 / UND-05: no agent event responds to the trigger -> J-REG; NOT_ACKNOWLEDGED -> defect.
    Returns True when the trigger was taken (a judgment or an SC-01 INCONCLUSIVE); False when it is registered."""
    assert plan.ex is not None
    if _responders(plan.ex, trigger.event_id):
        plan.det.facts.registered.setdefault(code, True)
        return False
    if _terminal(plan, code, trigger.turn, ni, spec):
        return True
    nxt = _next_agent(ni, trigger.turn)

    def resolve(answer: str) -> RuleOutput:
        out = RuleOutput()
        if answer == "NOT_ACKNOWLEDGED":
            out.findings.append(_finding(code, spec, _evs(_ev(trigger, element), _turn_ev(
                ni, nxt[0] if nxt else None, "next_agent_turn")), trigger.turn, Confidence.MEDIUM))
        elif answer != "ACKNOWLEDGED":
            return _check_out(code, f"J-REG {answer}")
        return out

    plan.pending.append(Pending("J-REG", trigger.event_id, [trigger.turn], resolve))
    return True


def und01_02_05(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    """One J-REG per code: the first unregistered trigger (registered triggers need no judgment)."""
    taken: set[str] = set()
    for e in sorted(ex.events, key=lambda e: e.turn):
        if e.type in UND01_TRIGGERS:
            code, element = "UND-01", "claim_turn"
        elif e.type in ("inability", "hardship_reason") and e.strength in (None, "explicit"):
            code, element = "UND-02", "statement_turn"
        elif e.type == "third_party_signal":
            if any(d.type == "account_disclosure" and d.turn > e.turn for d in ex.events):
                continue  # "If a disclosure follows, G1b applies instead."
            code, element = "UND-05", "signal_turn"
        else:
            continue
        if code not in taken and _jreg(plan, code, e, ni, spec, element):
            taken.add(code)


# ------------------------------------------------------------------------------------------ UND-03
def und03(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    """Borrower commitment value vs the agent's read-back, after normalization; no defect when the borrower's
    confirmation restates the agent's value."""
    for c in ex.commitments:
        if c.readback_turn is None or not c.readback_values_raw:
            continue
        rb = parse_values(c.readback_values_raw)
        conf = parse_values(c.borrower_confirmation_quote)
        mismatch: list[str] = []
        unknown: list[str] = []
        if c.amount_norm is not None:
            if rb.amounts:
                if c.amount_norm not in rb.amounts:
                    mismatch.append("amount")
            elif c.amount_raw:
                unknown.append("amount")
        parts = _date_parts(c.date_norm)
        if parts is not None:
            if rb.days:
                if parts[0] not in rb.days or (rb.months and parts[1] not in rb.months):
                    mismatch.append("date")
            elif c.date_raw:
                unknown.append("date")
        if mismatch:
            restated = (("amount" not in mismatch or set(rb.amounts) & set(conf.amounts)) and
                        ("date" not in mismatch or set(rb.days) & set(conf.days)))
            if restated:
                continue  # the borrower's confirmation explicitly restates the agent's value
            bturn = c.confirmed_turn or c.proposed_turn
            plan.det.findings.append(_finding("UND-03", spec, _evs(
                _turn_ev(ni, bturn, "borrower_value_turn"), _turn_ev(ni, c.readback_turn, "agent_restatement_turn")),
                c.readback_turn, Confidence.HIGH))
            plan.det.facts.det_confirmed["UND-03"] = True
            return
        if unknown:
            _inconclusive(plan, "UND-03", f"read-back {unknown} not normalizable without the numerals lexicon (B-04)")


# ------------------------------------------------------------------------------------------ UND-04 / ACC-04
def und04_acc04(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    for q in sorted((e for e in ex.events if e.type == "material_question"), key=lambda e: e.turn):
        prior = sorted((v for v in ex.agent_stated_values if v.turn < q.turn), key=lambda v: v.turn)
        code = "ACC-04" if prior else "UND-04"
        if _terminal(plan, code, q.turn, ni, spec):
            continue
        nxt = _next_agent(ni, q.turn)

        def resolve(answer: str, q: ExtractedEvent = q, prior=prior, nxt=nxt) -> RuleOutput:
            out = RuleOutput()
            if answer == "DEFLECTED" and prior:
                v = prior[-1]
                out.findings.append(_finding("ACC-04", spec, _evs(
                    Evidence(turn=v.turn, quote=v.quote, role=Role.AGENT, element="prior_statement_turn"),
                    _ev(q, "question_turn"), _turn_ev(ni, nxt[0] if nxt else None, "evasion_turn")),
                    q.turn, Confidence.MEDIUM))
            elif answer in ("DEFLECTED", "UNANSWERED"):
                out.findings.append(_finding("UND-04", spec, _evs(
                    _ev(q, "question_turn"), _turn_ev(ni, nxt[0] if nxt else None, "following_agent_turns")),
                    q.turn, Confidence.MEDIUM))
            elif answer != "ANSWERED":
                return _check_out("ACC-04" if prior else "UND-04", f"J-Q {answer}")
            return out

        plan.pending.append(Pending("J-Q", q.event_id, [q.turn], resolve))


# ------------------------------------------------------------------------------------------ UND-06
def und06(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    for r in (e for e in ex.events if e.type in ("human_request", "stop_request") and e.strength == "ambiguous"):
        handled = [e for e in _responders(ex, r.event_id)
                   if e.type == "route_action" or (e.type == "acknowledgment" and e.kind == "clarify")]
        if handled:
            plan.det.facts.registered.setdefault("UND-06", True)
            continue
        if _terminal(plan, "UND-06", r.turn, ni, spec):
            continue
        nxt = _next_agent(ni, r.turn)

        def resolve(answer: str, r: ExtractedEvent = r, nxt=nxt) -> RuleOutput:
            out = RuleOutput()
            if answer in ("YES", "AMBIGUOUS"):
                out.findings.append(_finding("UND-06", spec, _evs(
                    _ev(r, "request_turn"), _turn_ev(ni, nxt[0] if nxt else None, "next_agent_turn")),
                    r.turn, Confidence.MEDIUM))
            return out  # NO: not a rights request (J-RIGHTS has no CANNOT_DETERMINE)

        plan.pending.append(Pending("J-RIGHTS", r.event_id, [r.turn], resolve))
        return


# ------------------------------------------------------------------------------------------ UND-12 / RES-11
def und12_res11(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    first_commit = min((c.proposed_turn or c.confirmed_turn or 10**6 for c in ex.commitments), default=None)
    if first_commit is not None and any(e.type == "ask" and e.turn > first_commit for e in ex.events):
        _inconclusive(plan, "UND-12", "an ask follows a stated commitment; extraction 2.0.0 carries no ask slot")
    thresholds = spec.profile["thresholds"]
    min_turns = int(thresholds["loop_min_turns"]["value"])
    if len(_agent_turns(ni)) >= min_turns:
        if thresholds["loop_similarity"]["value"] == PENDING:
            _inconclusive(plan, "RES-11", "loop_similarity is PENDING_HUMAN_SIGNOFF (B-11)")
        else:  # pragma: no cover - the similarity method is part of the same sign-off
            _inconclusive(plan, "RES-11", "loop similarity method not implemented until B-11 names it")


# ------------------------------------------------------------------------------------------ RES-01 / RES-03
def res01(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    for t in sorted((e for e in ex.events if e.type in RES01_TRIGGERS), key=lambda e: e.turn):
        reg = _responders(ex, t.event_id)
        if not reg:
            continue  # RES-01 needs a registration (responds_to link)
        registration = reg[0]
        asks = sorted((e for e in ex.events if e.type == "ask" and e.turn >= registration.turn), key=lambda e: e.turn)

        def resolve(answer: str, t: ExtractedEvent = t, registration=registration, asks=asks) -> RuleOutput:
            out = RuleOutput()
            if answer == "B":
                pressing = [_ev(a, "pressing_turn") for a in asks] or _evs(_turn_ev(
                    ni, (_next_agent(ni, registration.turn) or [registration.turn])[0], "pressing_turn"))
                action = ActionType.REMEDIATE if t.type in RES01_REMEDIATE else ActionType.FIX
                out.findings.append(_finding("RES-01", spec, [_ev(registration, "registration_turn"), *pressing],
                                             pressing[0].turn, Confidence.MEDIUM, action=action))
            elif answer not in ("A", "C"):
                return _check_out("RES-01", f"J-PATH {answer}")
            return out

        plan.pending.append(Pending("J-PATH", t.event_id, [t.turn, registration.turn], resolve))
        return


def res03(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    for r in sorted((e for e in ex.events if e.type == "refusal"), key=lambda e: e.turn):
        if _terminal(plan, "RES-03", r.turn, ni, spec):
            continue
        nxt = _next_agent(ni, r.turn, 2)
        if _responders(ex, r.event_id):
            plan.det.facts.registered.setdefault("RES-03", True)

        def resolve(answer: str, r: ExtractedEvent = r, nxt=nxt) -> RuleOutput:
            out = RuleOutput()
            if answer == "IGNORED":
                out.findings.append(_finding("RES-03", spec, _evs(_ev(r, "objection_turn"), *[
                    _turn_ev(ni, n, "next_agent_turns") for n in nxt]), r.turn, Confidence.MEDIUM))
            elif answer not in ("ENGAGED", "PARTIAL"):
                return _check_out("RES-03", f"J-OBJ {answer}")
            return out

        plan.pending.append(Pending("J-OBJ", r.event_id, [r.turn], resolve))
        return


# ------------------------------------------------------------------------------------------ RES-02 / RES-04 / RES-05
def res02(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    affirmed = sorted(c.turn for c in ex.call_frame.identity_checks if c.result == "affirmed")
    if not affirmed or any(e.type in RES02_EXCLUDE for e in ex.events) or any(e.type == "ask" for e in ex.events):
        return
    stage = ex.call_frame.stage_hint
    if stage == "pre_due":
        plan.checks["RES-02"] = CheckStatusEntry(code="RES-02", status=CheckStatus.NA)
        return
    if stage != "overdue":
        _inconclusive(plan, "RES-02", f"stage_hint {stage}", ReasonCode.STAGE_UNKNOWN)
        return
    agents = _agent_turns(ni)
    plan.det.findings.append(_finding("RES-02", spec, _evs(
        _turn_ev(ni, affirmed[0], "identity_affirmation_turn"), _turn_ev(ni, agents[-1] if agents else None,
                                                                          "call_end_turn")),
        agents[-1] if agents else None, Confidence.MEDIUM))


def res04(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    triggers = set(spec.profile["escalation_triggers"]["triggers"])
    for t in sorted((e for e in ex.events if e.type in triggers), key=lambda e: e.turn):
        if _responders(ex, t.event_id):
            plan.det.facts.registered.setdefault("RES-04", True)
        if any(e.type == "route_action" and e.route in RES04_ROUTES and e.turn > t.turn for e in ex.events):
            continue
        if _terminal(plan, "RES-04", t.turn, ni, spec):
            return
        agents = _agent_turns(ni)
        plan.det.findings.append(_finding("RES-04", spec, _evs(
            _ev(t, "trigger_turn"), _turn_ev(ni, agents[-1] if agents else None, "call_end_turn")),
            t.turn, Confidence.MEDIUM))
        return


def res05(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    pending_q = ex.call_end.get("pending_borrower_question_turn")
    if not isinstance(pending_q, int) or ni.turn_by_number(pending_q) is None:
        return
    ended_by = ex.call_end.get("ended_by")
    if ended_by not in ("agent", "borrower"):
        _inconclusive(plan, "RES-05", f"ended_by {ended_by!r}")
        return
    if ended_by != "agent":
        return
    closing = [e.turn for e in ex.events if e.type == "ends_call" and e.turn > pending_q] or \
              [t for t in _agent_turns(ni) if t > pending_q][-1:]
    if not closing:
        return
    plan.det.findings.append(_finding("RES-05", spec, _evs(
        _turn_ev(ni, pending_q, "pending_question_turn"), _turn_ev(ni, closing[0], "closing_turn")),
        closing[0], Confidence.MEDIUM))


# ------------------------------------------------------------------------------------------ COM-*
def _firm_commitments(ex: ExtractionOutput, firmness: dict[str, str]) -> list[Commitment]:
    return [c for c in ex.commitments if firmness.get(c.id, c.firmness or "") == "firm"]


def _commitment_turn_of(c: Commitment) -> int | None:
    return c.confirmed_turn or c.proposed_turn


def com01(ex: ExtractionOutput, ni: NormalizedInput, spec: Spec, firmness: dict[str, str]) -> RuleOutput:
    """Firm commitment lacking a required field (profile.ptp.required_fields). A raw date or amount that the
    extraction did not normalize counts as present (the non_specific check needs the B-04 lexicon)."""
    out = RuleOutput()
    required = set(spec.profile["ptp"]["required_fields"])
    for c in _firm_commitments(ex, firmness):
        missing = [f for f in sorted(required) if (f == "date" and c.date_norm is None and not c.date_raw)
                   or (f == "amount" and c.amount_norm is None and not c.amount_raw)]
        turn = _commitment_turn_of(c)
        if missing and turn:
            out.findings.append(_finding("COM-01", spec, _evs(_turn_ev(ni, turn, "commitment_turn")), turn,
                                         Confidence.HIGH))
            out.facts.det_confirmed["COM-01"] = True
            out.trace.append({"rule": "COM-01", "commitment": c.id, "missing": missing})
            break
    return out


def com03(ex: ExtractionOutput, ni: NormalizedInput, spec: Spec, firmness: dict[str, str]) -> RuleOutput:
    """PTP (firm commitment) without a read-back, or without borrower confirmation after it -> MAJOR; a callback
    route without a later read-back event, or with no borrower turn after that read-back -> MINOR."""
    out = RuleOutput()
    for c in _firm_commitments(ex, firmness):
        turn = _commitment_turn_of(c)
        if turn and (c.readback_turn is None or not c.borrower_confirmation_quote):
            out.findings.append(_finding("COM-03", spec, _evs(_turn_ev(ni, turn, "commitment_turn")), turn,
                                         Confidence.HIGH, severity=Severity.MAJOR))
            out.facts.det_confirmed["COM-03"] = True
            return out
    for r in (e for e in ex.events if e.type == "route_action" and e.route == "callback"):
        readbacks = [e for e in ex.events if e.type == "readback" and e.turn >= r.turn]
        confirmed = any(any(t.role is Role.BORROWER and t.turn > rb.turn for t in ni.turns) for rb in readbacks)
        if not readbacks or not confirmed:
            out.findings.append(_finding("COM-03", spec, [_ev(r)], r.turn, Confidence.HIGH, severity=Severity.MINOR))
            out.facts.det_confirmed["COM-03"] = True
            return out
    return out


def com02(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    """J-FIRM on a commitment with a hedge or a non-firm extraction label; SOFT / CONDITIONAL while the agent treats
    it as firm (a read-back / note of the promise) -> COM-02. The answer also sets the commitment firmness."""
    for c in ex.commitments:
        if not c.hedge_quote and (c.firmness in (None, "firm")):
            continue
        turn = c.proposed_turn or c.confirmed_turn
        readbacks = [e.turn for e in ex.events if e.type == "readback" and (turn is None or e.turn > turn)]
        treat = c.readback_turn or (readbacks[0] if readbacks else None)

        def resolve(answer: str, c: Commitment = c, turn=turn, treat=treat) -> RuleOutput:
            out = RuleOutput()
            if answer in ("FIRM", "SOFT", "CONDITIONAL"):
                plan.firmness[c.id] = answer.lower()
            if answer in ("SOFT", "CONDITIONAL") and treat is not None:
                out.findings.append(_finding("COM-02", spec, _evs(_turn_ev(ni, turn, "hedge_turn"), _turn_ev(
                    ni, treat, "firm_treatment_turn")), turn, Confidence.MEDIUM))
            elif answer not in ("FIRM", "SOFT", "CONDITIONAL"):
                return _check_out("COM-02", f"J-FIRM {answer}")
            return out

        plan.pending.append(Pending("J-FIRM", c.id, [t for t in (turn, treat) if t], resolve))


def com05(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    """A situational_constraint carrying a date later than the commitment date, both normalized, both in this call.
    Constraint dates are read from the quote (digits only until B-04)."""
    commits = [c for c in ex.commitments if c.date_norm is not None or c.date_raw]
    constraints = [e for e in ex.events if e.type == "situational_constraint"]
    if not commits or not constraints:
        return
    for k in constraints:
        if _responders(ex, k.event_id):
            plan.det.facts.registered.setdefault("COM-05", True)
        days = parse_values(k.quote).days
        for c in commits:
            if c.date_norm is None:
                _inconclusive(plan, "COM-05", "commitment date not normalized")
                continue
            if not days:
                _inconclusive(plan, "COM-05", "constraint date not normalizable without the numerals lexicon (B-04)")
                continue
            if max(days) > c.date_norm.day:
                turn = _commitment_turn_of(c)
                plan.det.findings.append(_finding("COM-05", spec, _evs(_ev(k, "constraint_turn"), _turn_ev(
                    ni, turn, "commitment_turn")), turn, Confidence.HIGH))
                plan.det.facts.det_confirmed["COM-05"] = True
                plan.checks.pop("COM-05", None)
                return


def com06(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    routes = [e for e in ex.events if e.type == "route_action" and e.route in COM06_ROUTES]
    if not routes:
        return
    if "next_step_quote" not in ex.call_end:
        _inconclusive(plan, "COM-06", "extraction call_end has no next_step_quote")
        return
    if ex.call_end.get("next_step_quote"):
        return
    agents = _agent_turns(ni)
    plan.det.findings.append(_finding("COM-06", spec, _evs(_ev(routes[0], "route_turn"), _turn_ev(
        ni, agents[-1] if agents else None, "final_agent_turn")), agents[-1] if agents else None, Confidence.MEDIUM))


# ------------------------------------------------------------------------------------------ TRT-*
def trt01(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    firsts = sorted((e for e in ex.events if e.type in ("refusal", "inability") and e.strength in (None, "explicit")),
                    key=lambda e: e.turn)
    if not firsts:
        return
    first = firsts[0]
    asks = sorted((e for e in ex.events if e.type == "ask" and e.turn > first.turn), key=lambda e: e.turn)
    limit = int(spec.profile["asks"]["max_after_explicit_refusal_or_inability"])
    if len(asks) > limit:
        excess = asks[limit:]
        plan.det.findings.append(_finding("TRT-01", spec, [_ev(first, "refusal_or_inability_turn"), *[
            _ev(a, "excess_ask_turns") for a in excess]], excess[0].turn, Confidence.HIGH))
        plan.det.facts.det_confirmed["TRT-01"] = True


def trt02(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    for k in sorted((e for e in ex.events if e.type == "situational_constraint"), key=lambda e: e.turn):
        if _responders(ex, k.event_id):
            plan.det.facts.registered.setdefault("TRT-02", True)
        if _terminal(plan, "TRT-02", k.turn, ni, spec):
            continue
        nxt = _next_agent(ni, k.turn)

        def resolve(answer: str, k: ExtractedEvent = k, nxt=nxt) -> RuleOutput:
            out = RuleOutput()
            if answer == "NO":
                out.findings.append(_finding("TRT-02", spec, _evs(_ev(k, "constraint_turn"), _turn_ev(
                    ni, nxt[0] if nxt else None, "next_agent_turn")), k.turn, Confidence.MEDIUM))
            elif answer not in ("YES", "NOT_NEEDED"):
                return _check_out("TRT-02", f"J-CONSTR {answer}")
            return out

        plan.pending.append(Pending("J-CONSTR", k.event_id, [k.turn], resolve))


def _compatible(a: str, b: str) -> bool:
    """hi-en (code-mixed Hinglish) is compatible with hi and with en (convention, §3.46)."""
    return a == b or "hi-en" in (a, b) and {a, b} <= {"hi", "en", "hi-en"}


def trt03(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    langs = {int(k): v for k, v in ex.turn_languages.items() if str(k).isdigit()}
    if not langs:
        _inconclusive(plan, "TRT-03", "extraction carries no turn_languages")
        return
    supported = set(spec.profile["languages_supported"])
    borrower = [t.turn for t in ni.turns if t.role is Role.BORROWER]
    agents = _agent_turns(ni)
    switch: tuple[int, str] | None = None
    req = next((e for e in ex.events if e.type == "language_request"), None)
    if req is not None and req.turn in langs:
        switch = (req.turn, langs[req.turn])
    for a, b in zip(borrower, borrower[1:], strict=False):
        la, lb = langs.get(a), langs.get(b)
        if la and la == lb and la in supported:
            prev_agent = [t for t in agents if t < a]
            if prev_agent and langs.get(prev_agent[-1]) and not _compatible(langs[prev_agent[-1]], la):
                if switch is None or b < switch[0]:
                    switch = (b, la)
                break
    if switch is None:
        return
    after = [t for t in agents if t > switch[0]][:2]
    if len(after) == 2 and all(langs.get(t) and not _compatible(langs[t], switch[1]) for t in after):
        plan.det.findings.append(_finding("TRT-03", spec, _evs(_turn_ev(ni, switch[0], "switch_turn"), *[
            _turn_ev(ni, t, "mismatched_agent_turns") for t in after]), switch[0], Confidence.HIGH))
        plan.det.facts.det_confirmed["TRT-03"] = True


# ------------------------------------------------------------------------------------------ POL-01a / PLT
def _org_statement_turns(ex: ExtractionOutput) -> list[int]:
    raw = ex.call_frame.agent_org_statement
    items = raw if isinstance(raw, list) else [raw]
    return sorted(int(i["turn"]) for i in items if isinstance(i, dict) and isinstance(i.get("turn"), int))


def pol01a(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    for d in spec.profile["disclosures"]["expected"]:
        if d.get("code") != "POL-01a":
            continue
        window = _agent_turns(ni)[: int(d["within_agent_turns"])]
        if not window:
            return
        if any(t <= window[-1] for t in _org_statement_turns(ex)):
            continue
        sev = Severity(d.get("severity", "MAJOR"))

        def resolve(answer: str, window=window, sev=sev) -> RuleOutput:
            out = RuleOutput()
            if answer == "ABSENT":
                out.findings.append(_finding("POL-01", spec, _evs(_turn_ev(ni, window[-1], "window_or_statement_turn")),
                                             window[-1], Confidence.MEDIUM, severity=sev, sub_rule="POL-01a"))
            elif answer != "PRESENT":
                return _check_out("POL-01", f"J-G8P {answer}")
            return out

        plan.pending.append(Pending("J-G8P", str(d["id"]), window, resolve))


def platform(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    thresholds = spec.profile["thresholds"]
    if ni.has_timestamps:  # PLT-01: response gap followed by a recontact cue or call end
        gap_max = float(thresholds["response_gap_max_seconds"]["value"])
        cues = spec.lexicon_terms("recontact_cues")
        turns = ni.turns
        for i in range(1, len(turns)):
            prev, cur = turns[i - 1], turns[i]
            if prev.role is Role.BORROWER and cur.role is Role.AGENT and prev.end_s is not None and \
                    cur.start_s is not None and cur.start_s - prev.end_s > gap_max:
                follow = turns[i + 1] if i + 1 < len(turns) else None
                cue = follow is not None and follow.role is Role.BORROWER and any(
                    c.lower() in follow.text.lower() for c in cues)
                if follow is None or cue:
                    anchor = follow.turn if follow is not None else cur.turn
                    plan.det.findings.append(_finding("PLT-01", spec, _evs(_turn_ev(ni, anchor)), anchor,
                                                      Confidence.HIGH, severity=Severity.MINOR))
                    plan.det.facts.det_confirmed["PLT-01"] = True
                    break
    if ni.input_mode is not InputMode.TRANSCRIPT and thresholds["overlap_min_seconds"]["value"] == PENDING:
        _inconclusive(plan, "PLT-02", "overlap_min_seconds is PENDING_HUMAN_SIGNOFF (B-11)")
    if ni.input_mode is InputMode.AUDIO_TRANSCRIPT and \
            ni.header.transcript_provenance is TranscriptProvenance.PLATFORM_LIVE_ASR:
        for code in ("PLT-03", "PLT-04"):
            _inconclusive(plan, code, "SAID vs HEARD comparison not built")


# ------------------------------------------------------------------------------------------ G2/G3 J-CONS, G6
def jcons(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    maps = spec.rubric["judgments"]["J-CONS"]["maps"]
    for eid in [t for (j, t, _turns) in plan.det.judgments if j == "J-CONS"]:
        c = ex.event(eid)
        if c is None:
            continue

        def resolve(answer: str, c: ExtractedEvent = c) -> RuleOutput:
            out = RuleOutput()
            gate = maps.get(answer)
            if gate:
                out.gates[gate] = GateResult(gate=GateId(gate), status=GateStatus.FAIL,
                                             critical_status=CriticalStatus.SUSPECTED, confidence=Confidence.MEDIUM,
                                             evidence=[_ev(c, "consequence_statement_turn" if gate == "G2"
                                                           else "threat_turn")],
                                             sub_rule="G2a" if gate == "G2" else None)
            elif answer == CANNOT:
                out.gates["G2"] = GateResult(gate=GateId.G2, status=GateStatus.INCONCLUSIVE, in_span_trigger=True,
                                             evidence=[_ev(c, "consequence_statement_turn")], sub_rule="G2a")
            else:
                out.gates["G2"] = GateResult(gate=GateId.G2, status=GateStatus.PASS)
            return out

        plan.pending.append(Pending("J-CONS", c.event_id, [c.turn], resolve))
    plan.det.judgments = [j for j in plan.det.judgments if j[0] != "J-CONS"]


def g6(plan: Plan, ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> None:
    cues = sorted((e for e in ex.events if e.type == "vulnerability_cue"), key=lambda e: e.turn)
    if not cues:
        plan.det.gates["G6"] = GateResult(gate=GateId.G6, status=GateStatus.NA)
        return
    collection = set(spec.rubric["extraction_vocabulary"]["collection_content_agent_events"])
    for cue in cues:
        if _responders(ex, cue.event_id):
            plan.det.facts.registered.setdefault("G6", True)
        if _terminal(plan, "G6", cue.turn, ni, spec):
            continue
        window = _next_agent(ni, cue.turn, 2)
        coll = sorted((e for e in ex.events if e.side is Role.AGENT and e.type in collection and e.turn in window),
                      key=lambda e: e.turn)
        routed = [e.turn for e in ex.events if e.type == "route_action" and e.route in G6_CARE_ROUTES
                  and e.turn in window]
        violating = [e for e in coll if not any(r <= e.turn for r in routed)]

        def resolve(answer: str, cue: ExtractedEvent = cue, violating=violating) -> RuleOutput:
            out = RuleOutput()
            severity = next((s for s in re.split(r"[|;,/ ]+", answer) if s), CANNOT)
            out.trace.append({"rule": "G6", "cue": cue.event_id, "severity": severity})
            if severity == "DISTRESS_NONEXPLICIT":
                out.gates["G6"] = GateResult(gate=GateId.G6, status=GateStatus.NA)
            elif severity in G6_EXPLICIT:
                if severity == "DEATH_REPORTED":
                    out.trace.append({"disposition": "DEATH_REPORTED"})
                if violating:
                    out.gates["G6"] = GateResult(gate=GateId.G6, status=GateStatus.FAIL,
                                                 critical_status=CriticalStatus.SUSPECTED, confidence=Confidence.MEDIUM,
                                                 evidence=[_ev(cue, "cue_turn"),
                                                           _ev(violating[0], "subsequent_collection_turn")])
                else:
                    out.gates["G6"] = GateResult(gate=GateId.G6, status=GateStatus.PASS)
            else:  # CANNOT_DETERMINE: "gate SUSPECTED (never PASS)"
                out.gates["G6"] = GateResult(gate=GateId.G6, status=GateStatus.INCONCLUSIVE, in_span_trigger=True,
                                             evidence=[_ev(cue, "cue_turn")])
            return out

        plan.pending.append(Pending("J-G6", cue.event_id, [cue.turn], resolve))


# ------------------------------------------------------------------------------------------ plan / resolve
PLAN_RULES = (und01_02_05, und03, und04_acc04, und06, und12_res11, res01, res02, res03, res04, res05, com02, com05,
              com06, trt01, trt02, trt03, pol01a, platform, jcons, g6)


def plan(ex: ExtractionOutput, ni: NormalizedInput, spec: Spec) -> Plan:
    p = Plan(det=apply_implemented(ex, ni, spec), ex=ex, ni=ni)
    for rule in PLAN_RULES:
        rule(p, ex, ni, spec)
    return p


def resolve(p: Plan, answers: dict[tuple[str, str], str], spec: Spec) -> tuple[RecordBody, Facts, list[dict]]:
    """Apply judgment answers ((judgment, target) -> answer; absent = CANNOT_DETERMINE) and assemble the body."""
    assert p.ex is not None and p.ni is not None
    ex, ni = p.ex, p.ni
    out = RuleOutput()
    out.merge(p.det)
    checks = dict(p.checks)
    log: list[dict] = []
    resolved: list[RuleOutput] = []
    for pend in p.pending:
        ans = answers.get((pend.judgment, pend.target), CANNOT)
        r = pend.resolve(ans)
        log.append({"judgment": pend.judgment, "target": pend.target, "answer": ans})
        resolved.append(r)
    firmness = p.firmness
    for extra in (com01(ex, ni, spec, firmness), com03(ex, ni, spec, firmness)):
        resolved.append(extra)
    gates: dict[str, GateResult] = {}
    for gid, g in out.gates.items():
        gates[gid] = merge_gate(gates.get(gid), g)
    findings = list(out.findings)
    death = False
    for r in resolved:
        for gid, g in r.gates.items():
            gates[gid] = merge_gate(gates.get(gid), g)
        findings += r.findings
        for name in ("det_confirmed", "registered"):
            getattr(out.facts, name).update(getattr(r.facts, name))
        for t in r.trace:
            if t.get("status") == "INCONCLUSIVE" and t["check"] not in checks:
                checks[t["check"]] = CheckStatusEntry(code=t["check"], status=CheckStatus.INCONCLUSIVE,
                                                      reason_codes=[ReasonCode(x) for x in t.get("reason_codes", [])])
            death = death or t.get("disposition") == "DEATH_REPORTED"
        log += r.trace
    if "G2" not in gates:  # every consequence statement resolved, none failing
        consequences = [e for e in ex.events if e.type == "consequence_statement"]
        asks_ai = [e for e in ex.events if e.type == "asks_if_ai"]
        gates["G2"] = GateResult(gate=GateId.G2, status=GateStatus.PASS if (consequences or asks_ai)
                                 else GateStatus.NA)
    gates.setdefault("G3", GateResult(gate=GateId.G3, status=GateStatus.PASS))  # G3 is never NA in an evaluable call
    gates.setdefault("G6", GateResult(gate=GateId.G6, status=GateStatus.NA))
    emitted = {f.code for f in findings}
    for code in list(checks):
        if code in emitted:
            checks.pop(code)
    outcome = build_outcome(ex, ni, spec, firmness, findings, gates, death)
    # EXE-03 is out of scope (EXTERNAL_DATA_REQUIRED); its mvp_output lists the agent's promise_of_action events as
    # unverified commitments (verified quotes only: the extraction verifier has already dropped the rest)
    commitments = [_ev(e, "promise_of_action") for e in ex.events if e.type == "promise_of_action"]
    body = RecordBody(gates=list(gates.values()), findings=findings, checks=list(checks.values()), outcome=outcome,
                      unverified_agent_commitments=commitments)
    return body, out.facts, log + p.notes


def build_outcome(ex: ExtractionOutput, ni: NormalizedInput, spec: Spec, firmness: dict[str, str],
                  findings: list[Finding], gates: dict[str, GateResult], death: bool) -> Outcome:
    """rubric.yaml › outcome_model (observable only; descriptive). Mapping conventions in §3.46."""
    types = {e.type for e in ex.events}
    d: list[Disposition] = []
    add = d.append
    if ex.call_frame.non_conversation:
        add(Disposition.NON_CONVERSATIONAL)
    if ex.commitments:
        add(Disposition.PTP_STATED)
    for t, disp in (("payment_claim", Disposition.PAYMENT_CLAIMED_ALREADY_PAID),
                    ("dispute_amount", Disposition.DISPUTE_RAISED), ("dispute_liability", Disposition.DISPUTE_RAISED),
                    ("inability", Disposition.HARDSHIP_OR_INABILITY_STATED),
                    ("hardship_reason", Disposition.HARDSHIP_OR_INABILITY_STATED),
                    ("settlement_request", Disposition.SETTLEMENT_REQUESTED), ("human_request", Disposition.HUMAN_REQUESTED),
                    ("refusal", Disposition.REFUSED), ("stop_request", Disposition.STOP_REQUESTED),
                    ("third_party_signal", Disposition.THIRD_PARTY_REACHED),
                    ("complaint_prior_conduct", Disposition.COMPLAINT_RAISED)):
        if t in types and disp not in d:
            add(disp)
    if any(e.type == "offer" and e.accepted_turn for e in ex.events):
        add(Disposition.OFFER_AGREED)
    routes = {e.route for e in ex.events if e.type == "route_action"}
    if "callback" in routes:
        add(Disposition.CALLBACK_AGREED)
    if "human_transfer" in routes:
        add(Disposition.TRANSFER_ANNOUNCED)
    if death:
        add(Disposition.DEATH_REPORTED)
    if not d:
        affirmed = any(c.result == "affirmed" for c in ex.call_frame.identity_checks)
        add(Disposition.INCOMPLETE if ex.call_end.get("ended_by") == "borrower" or not affirmed
            else Disposition.ACKNOWLEDGED)
    firm_vals = [firmness.get(c.id, c.firmness) for c in ex.commitments]
    firm = next((f for f in firm_vals if f == "firm"), next((f for f in firm_vals if f), None))
    fir = Firmness(firm) if firm in {f.value for f in Firmness} else None
    pos = spec.registry.outcome_positive([x.value for x in d], fir.value if fir else None)
    fired = any(g.status is GateStatus.FAIL for g in gates.values())
    if pos:
        oa = OutcomeAttribution.AGENT_DRIVEN
    elif routes & {"dispute", "hardship", "escalation", "stop_honored", "human_transfer", "callback"}:
        oa = OutcomeAttribution.POLICY_DRIVEN
    elif (types & {"refusal", "inability", "dispute_amount", "dispute_liability", "third_party_signal"}) and not fired \
            and not any(f.severity is Severity.MAJOR for f in findings):
        oa = OutcomeAttribution.CUSTOMER_DRIVEN
    else:
        oa = OutcomeAttribution.INDETERMINATE
    return Outcome(dispositions=d, positive=pos, commitment_turn=commitment_turn(ex), firmness=fir,
                   outcome_attribution=oa)


__all__ = ["CANNOT", "Pending", "Plan", "build_outcome", "merge_gate", "parse_values", "plan", "resolve"]
