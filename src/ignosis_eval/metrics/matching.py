"""Matching evaluator findings/gates/evidence to gold expectations (schema-level only).

Detection of an expected defect D in a record (PROVISIONAL, see docs/reliability.md §matching):
  * a finding with the same defect_id, OR
  * D has a gate_id and the record's gate with that id has status FAIL.
Matching is by declared taxonomy ids; no text similarity or evaluator-specific heuristics are used.
"""

from __future__ import annotations

from ignosis_eval.contracts.enums import EvidenceModality, GateStatus, Severity
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, EvidenceItem, Finding
from ignosis_eval.contracts.gold_label import ExpectedDefect, GoldLabel


def findings_for(record: EvaluationRecord | None, defect_id: str) -> list[Finding]:
    return [] if record is None else [f for f in record.findings if f.defect_id == defect_id]


def gate_status(record: EvaluationRecord | None, gate_id: str) -> GateStatus | None:
    if record is None:
        return None
    g = record.gate(gate_id)
    return g.status if g else None


def detected(record: EvaluationRecord | None, defect_id: str, gate_id: str | None) -> bool:
    if record is None:
        return False
    if findings_for(record, defect_id):
        return True
    return gate_id is not None and gate_status(record, gate_id) is GateStatus.FAIL


def is_detected(record: EvaluationRecord | None, exp: ExpectedDefect) -> bool:
    return detected(record, exp.defect_id, exp.gate_id)


def required_defects(gold: GoldLabel, severity: Severity | None = None) -> list[ExpectedDefect]:
    return [d for d in gold.expected_defects if d.required and (severity is None or d.severity is severity)]


def _evidence_items_for(record: EvaluationRecord, exp: ExpectedDefect) -> list[EvidenceItem]:
    emap = record.evidence_map()
    ids: list[str] = []
    for f in findings_for(record, exp.defect_id):
        ids += f.evidence_ids
    if not ids and exp.gate_id:
        g = record.gate(exp.gate_id)
        if g is not None and g.status is GateStatus.FAIL:
            ids += g.evidence_ids
    return [emap[i] for i in dict.fromkeys(ids) if i in emap]


def evidence_coverage(record: EvaluationRecord, exp: ExpectedDefect) -> tuple[int, int]:
    """(covered, required) evidence units for a detected expected defect.

    Units = required turn ids + required metadata fields + required audio spans (a span is covered by any
    overlapping audio evidence).
    """
    items = _evidence_items_for(record, exp)
    turns = {t for e in items for t in e.turn_ids}
    meta = {e.metadata_field for e in items if e.modality is EvidenceModality.METADATA}
    spans = [(e.start_ms, e.end_ms) for e in items if e.modality is EvidenceModality.AUDIO]
    req = exp.evidence
    covered = sum(t in turns for t in req.required_turn_ids)
    covered += sum(m in meta for m in req.metadata_fields)
    covered += sum(any(s < ge and gs < e for s, e in spans) for gs, ge in req.audio_spans_ms)
    required = len(req.required_turn_ids) + len(req.metadata_fields) + len(req.audio_spans_ms)
    return covered, required


def best_finding(record: EvaluationRecord | None, exp: ExpectedDefect) -> Finding | None:
    """The finding for exp.defect_id whose evidence overlaps the gold turns most (ties: first)."""
    cands = findings_for(record, exp.defect_id)
    if not cands:
        return None
    emap = record.evidence_map()
    target = set(exp.evidence.required_turn_ids) | set(exp.evidence.acceptable_turn_ids)

    def overlap(f: Finding) -> int:
        return len({t for i in f.evidence_ids if i in emap for t in emap[i].turn_ids} & target)

    return max(cands, key=overlap)


def is_unsupported_finding(f: Finding, gold: GoldLabel) -> bool:
    return f.defect_id not in gold.all_expected_defect_ids and f.defect_id not in gold.acceptable_extra_defects
