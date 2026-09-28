"""Finding matching (SD-12), quote faithfulness (SD-13) and evidence completeness (SD-14)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.evaluation_record import Evidence
from ignosis_eval.contracts.evidence import quote_score
from ignosis_eval.metrics.alignment import FindingObs, RepObs

ANCHOR_TOLERANCE = 1
SEVERITY_RANK = {"INFORMATIONAL": 0, "MINOR": 1, "MAJOR": 2, "CRITICAL": 3}


def within(turns: Iterable[int], anchors: Iterable[int], tol: int = ANCHOR_TOLERANCE) -> bool:
    anchors = list(anchors)
    return any(abs(t - a) <= tol for t in turns for a in anchors)


@dataclass(frozen=True)
class MatchResult:
    tp: bool
    fp: bool
    fn: bool
    evaluator_turns: tuple[int, ...]


def match_code(rep: RepObs, code: str, gold_anchors: tuple[int, ...]) -> MatchResult:
    """SD-12. E = union of evidence turns over ASSERTED findings with the code (duplicates collapse);
    Match iff A and E are non-empty and some t in E is within ±1 of some a in A. TP = Match;
    FP = E non-empty and no Match; FN = A non-empty and no Match (a wrong anchor is FP and FN).
    An EVALUATION_FAILED rep has E = ∅, so every gold finding in it is FN."""
    e: set[int] = set()
    for f in rep.asserted(code):
        e.update(f.turns)
    m = bool(gold_anchors) and bool(e) and within(e, gold_anchors)
    return MatchResult(tp=m, fp=bool(e) and not m, fn=bool(gold_anchors) and not m, evaluator_turns=tuple(sorted(e)))


def evaluator_severity(rep: RepObs, code: str) -> str | None:
    """Maximum post-repair severity among the collapsed ASSERTED findings (SD-12 severity mismatch)."""
    sev = [f.severity for f in rep.asserted(code)]
    return max(sev, key=SEVERITY_RANK.__getitem__) if sev else None


def matching_finding(rep: RepObs, code: str, gold_anchors: tuple[int, ...]) -> FindingObs | None:
    """The first ASSERTED finding (record order) that cites a turn within ±1 of a gold anchor."""
    return next((f for f in rep.asserted(code) if within(f.turns, gold_anchors)), None)


def gate_matched(rep: RepObs, gate: str, gold_status: str, gold_anchors: tuple[int, ...]) -> bool:
    """SD-12 gate evidence matching: a fired gate whose cited turns are within ±1 of a gold anchor.
    G7 (anchor `header`) matches on firing alone. Only gold-FAIL gates can be matched."""
    if gate not in rep.fired or gold_status != "FAIL":
        return False
    if gate == "G7":
        return True
    return within(rep.gates[gate].turns, gold_anchors)


# ------------------------------------------------------------------------------------------- SD-13
def reference_text(ev: Evidence, ni: NormalizedInput) -> str | None:
    t = ni.turn_by_number(ev.turn) if ev.turn is not None else None
    if t is None:
        return None
    if ev.source == "supplied":
        return t.supplied_text
    if ev.source == "asr":
        return t.asr_text
    return t.text


def faithful(ev: Evidence, ni: NormalizedInput, quote_match_min: float) -> tuple[bool, str | None]:
    """Faithful iff the turn exists, evidence.role equals the turn's role and score >= quote_match_min."""
    t = ni.turn_by_number(ev.turn) if ev.turn is not None else None
    if t is None:
        return False, "turn does not exist"
    if ev.role is not t.role:
        return False, "role mismatch"
    ref = reference_text(ev, ni)
    if ref is None:
        return False, f"no {ev.source} text for the turn"
    score = quote_score(ev.quote or "", ref)
    return (score >= quote_match_min), (None if score >= quote_match_min else f"score {score:.1f}")


def quotes(rep: RepObs) -> list[dict[str, Any]]:
    """Every quote in a rep (findings and gate evidence; header evidence carries no quote)."""
    out: list[dict[str, Any]] = []
    for f in rep.findings:
        for ev in f.evidence:
            if ev.header_field is None:
                out.append({"kind": "finding", "check": f.code, "evidence": ev, "flagged": f.evidence_unverified})
    for g, o in rep.gates.items():
        for ev in o.evidence:
            if ev.header_field is None:
                out.append({"kind": "gate", "check": g, "evidence": ev, "flagged": o.evidence_unverified})
    return out


# ------------------------------------------------------------------------------------------- SD-14
def required_elements(rubric_check: dict[str, Any], gold_elements: dict[str, int], dangerous_win: str | None
                      ) -> tuple[list[str], list[str]]:
    """Required evidence elements for one gold finding: the check's elements minus `absence_allowed`,
    `only_for` elements only when their condition holds in gold (dangerous_win: gold DW != NONE; a sub-rule
    such as G1b: the labeler recorded that element), and sub-rule elements for the sub-rules whose elements
    the labeler recorded. Header elements (role none, G7) need no gold turn.
    Returns (scorable required names, required names missing a gold turn)."""
    names: list[str] = []
    for e in rubric_check.get("required_evidence_elements") or []:
        if e.get("absence_allowed"):
            continue
        cond = e.get("only_for")
        if cond == "dangerous_win" and dangerous_win in (None, "NONE"):
            continue
        if cond and cond != "dangerous_win" and e["name"] not in gold_elements:
            continue
        names.append(e["name"])
    subs = rubric_check.get("sub_rules")
    if isinstance(subs, dict):
        for body in subs.values():
            if not isinstance(body, dict):
                continue
            els = [e for e in body.get("required_evidence_elements") or [] if not e.get("absence_allowed")]
            if any(e["name"] in gold_elements for e in els):
                names.extend(e["name"] for e in els)
    header = {e["name"] for e in rubric_check.get("required_evidence_elements") or [] if e.get("role") == "none"}
    with_turn = [n for n in names if n in gold_elements or n in header]
    return with_turn, [n for n in names if n not in with_turn]


def completeness(cited_turns: Iterable[int], header_cited: bool, required: list[str], gold_elements: dict[str, int],
                 header_elements: frozenset[str] = frozenset({"header_call_start_ts"})) -> tuple[int, int]:
    """(satisfied, required): an element is satisfied iff a cited turn is within ±1 of its gold turn.
    The G7 header element is satisfied by header evidence."""
    cited = list(cited_turns)
    sat = 0
    for name in required:
        if name in header_elements:
            sat += header_cited
        elif within(cited, [gold_elements[name]]):
            sat += 1
    return sat, len(required)
