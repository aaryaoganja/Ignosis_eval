"""Computed confidence (frozen-contract §6, rubric.yaml › confidence_rules; R-01, R-11, R-12).

HIGH requires ALL of: verified quote from the correct speaker, all cited spans reliable, and either a
deterministic confirmation or a prohibited-lexicon hit without negation (codes whose `high_requires` is
`lexicon_hit_without_negation` — G2a, G3 — accept only the lexicon path). MEDIUM is the ceiling for
LLM-judged and absence-based codes (encoded as their rubric `confidence_ceiling`). LOW when any cited
span is unreliable, sources conflict, or a citation's role does not match its turn (architecture_application.A_plus
step 4, shared by B; AJ-05: role confidence is call-level only; an UNKNOWN or low-diarization turn is an unreliable
span). The model's own label can lower the result, never raise it. Used by A+, B and K0
(confidence_source COMPUTED); A reports SELF_REPORTED labels untouched (AJ-06).
"""

from __future__ import annotations

import re

from ignosis_eval.contracts.enums import (
    Confidence,
    CriticalStatus,
    FindingState,
    GateStatus,
    Severity,
    min_confidence,
)
from ignosis_eval.spec.registry import CheckDef

_RULE = re.compile(r"(POL-01[ab]|G\d[a-c]?)\s+(HIGH|MEDIUM|LOW)")


class ConfidenceError(RuntimeError):
    pass


def ceiling(cd: CheckDef, sub_rule: str | None = None) -> Confidence:
    if cd.confidence_rule:
        rules = {m.group(1): Confidence(m.group(2)) for m in _RULE.finditer(cd.confidence_rule)}
        if sub_rule in rules:
            return rules[sub_rule]
        if rules:
            return min_confidence(*rules.values())  # sub-rule unknown: the most conservative ceiling
    if sub_rule and sub_rule in cd.sub_rule_ceilings:
        return cd.sub_rule_ceilings[sub_rule]
    if cd.confidence_ceiling is not None:
        return cd.confidence_ceiling
    if cd.sub_rule_ceilings:
        return min_confidence(*cd.sub_rule_ceilings.values())
    raise ConfidenceError(f"{cd.id}: no confidence ceiling in the rubric")


def compute(cd: CheckDef, *, sub_rule: str | None, quote_ok: bool, role_ok: bool, spans_reliable: bool,
            det_confirmed: bool, lexicon_hit_without_negation: bool, contradictory: bool = False,
            llm_label: Confidence | None = None) -> Confidence:
    requirement = cd.high_requires.get(sub_rule or "", cd.high_requires.get(""))
    if requirement == "lexicon_hit_without_negation":
        high_basis = lexicon_hit_without_negation
    else:
        high_basis = det_confirmed or lexicon_hit_without_negation
    if not spans_reliable or contradictory or not role_ok:  # A_plus.4_confidence_cap (B: same module)
        level = Confidence.LOW
    elif quote_ok and role_ok and high_basis:
        level = Confidence.HIGH
    else:
        level = Confidence.MEDIUM
    level = min_confidence(level, ceiling(cd, sub_rule))
    if llm_label is not None:
        level = min_confidence(level, llm_label)  # may lower, never raise
    return level


def route_gate(status: GateStatus, confidence: Confidence | None, in_span_trigger: bool
               ) -> tuple[GateStatus, CriticalStatus | None]:
    """§6.6 routing: FAIL+HIGH -> CONFIRMED; FAIL+MEDIUM/LOW -> SUSPECTED; INCONCLUSIVE+in-span trigger ->
    FAIL/SUSPECTED; INCONCLUSIVE without trigger stays INCONCLUSIVE."""
    if status is GateStatus.FAIL:
        return GateStatus.FAIL, (CriticalStatus.CONFIRMED if confidence is Confidence.HIGH else CriticalStatus.SUSPECTED)
    if status is GateStatus.INCONCLUSIVE and in_span_trigger:
        return GateStatus.FAIL, CriticalStatus.SUSPECTED
    return status, None


def finding_state(severity: Severity, confidence: Confidence) -> FindingState:
    """Major + LOW -> POSSIBLE (does not set the verdict); everything else ASSERTED."""
    if severity is Severity.MAJOR and confidence is Confidence.LOW:
        return FindingState.POSSIBLE
    return FindingState.ASSERTED
