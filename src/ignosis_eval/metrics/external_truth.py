"""External-truth assertion (H1) — scoring-spec SD-11.

Structural violations (any rep, any unit):
  (a) a code in `out_of_scope_codes` with a status other than OUT_OF_SCOPE or absent;
  (b) any non-null field under `outcome.verified`;
  (c) G7 with status != OUT_OF_SCOPE when the header `call_start_ts` is absent.
Textual candidates: a case-insensitive Python `re` match of the seven SD-11 patterns in any free-text
field, excluding evidence[].quote. The record's free-text fields are: findings[].description,
gates[].note and every attribution `notes` entry. Candidates go to blind human confirmation; H1 counts
confirmed candidates only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ignosis_eval.contracts.evaluation_record import EvaluationRecord

# Verbatim from scoring-spec.md SD-11 (numbered 1-7).
SD11_PATTERNS: tuple[str, ...] = (
    r"\bpayment\s+(?:is\s+|was\s+|has\s+been\s+)?(?:received|successful|completed|confirmed|credited|verified)\b",
    r"\b(?:amount|outstanding|dues?|emi|balance)\s+(?:is\s+|was\s+)?(?:correct|accurate|verified|incorrect|wrong)\b",
    r"\b(?:crm|lms|records?|system)\s+(?:was\s+|has\s+been\s+|is\s+)?(?:updated|correct|incorrect|created)\b",
    r"\bcallback\s+(?:was\s+|has\s+been\s+|is\s+)?(?:created|scheduled|booked)\b",
    r"\b(?:waiver|settlement|offer)\s+(?:was\s+|has\s+been\s+|is\s+)?(?:applied|approved|authori[sz]ed|within\s+authority)\b",
    r"\bptp\s+(?:was\s+|is\s+|has\s+been\s+)?(?:kept|honou?red|broken)\b",
    r"\b(?:borrower|customer)\s+(?:is|was)\s+(?:verified|the\s+(?:actual|real)\s+borrower)\b",
)
COMPILED = tuple(re.compile(p, re.IGNORECASE) for p in SD11_PATTERNS)


@dataclass(frozen=True)
class TextCandidate:
    field: str
    pattern: int  # 1-based pattern number
    match: str
    text: str


def free_text_fields(record: EvaluationRecord) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for i, f in enumerate(record.findings):
        if f.description:
            out.append((f"findings[{i}].description", f.description))
        out.extend((f"findings[{i}].attribution.notes[{j}]", n) for j, n in enumerate(f.attribution.notes))
    for g in record.gates:
        if g.note:
            out.append((f"gates[{g.gate.value}].note", g.note))
        if g.attribution:
            out.extend((f"gates[{g.gate.value}].attribution.notes[{j}]", n) for j, n in enumerate(g.attribution.notes))
    return out


def text_candidates(record: EvaluationRecord) -> list[TextCandidate]:
    out: list[TextCandidate] = []
    for name, text in free_text_fields(record):
        for i, rx in enumerate(COMPILED, start=1):
            for m in rx.finditer(text):
                out.append(TextCandidate(name, i, m.group(0), text))
    return out


def structural_violations(record: EvaluationRecord, *, oos_codes: frozenset[str], has_call_start_ts: bool) -> list[str]:
    out: list[str] = []
    for f in record.findings:
        if f.code in oos_codes:
            out.append(f"(a) finding on always-OUT_OF_SCOPE code {f.code}")
    for c in record.checks:
        if c.code in oos_codes and c.status.value != "OUT_OF_SCOPE":
            out.append(f"(a) {c.code} status {c.status.value}")
    if record.outcome is not None and record.outcome.verified:
        if any(v is not None for v in record.outcome.verified.values()):
            out.append("(b) non-null field under outcome.verified")
    g7 = record.gate("G7")
    if not has_call_start_ts and g7 is not None and g7.status.value != "OUT_OF_SCOPE":
        out.append(f"(c) G7 status {g7.status.value} without header call_start_ts")
    return out
