"""Translate a judgement dict (LLM/mock/heuristic output) into RecordBuilder calls.

Malformed judgements raise EvaluatorOutputError: the runner records the error and writes no record,
which the scorer counts as an integrity failure (missing record). Nothing is silently repaired.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ignosis_eval.contracts.evaluation_record import AttributionClaim
from ignosis_eval.evaluators.builder import RecordBuilder


class EvaluatorOutputError(ValueError):
    """The evaluator's model output could not be turned into a valid Evaluation Record."""


def apply_judgement(b: RecordBuilder, j: dict[str, Any]) -> float | None:
    try:
        ev = j.get("evaluability") or {}
        b.set_evaluability(ev.get("status", "evaluable"), ev.get("reasons"))
        for g in j.get("gates", []):
            b.set_gate(g["gate_id"], g["status"], b.add_evidence_dicts(g.get("evidence", [])), g.get("confidence"),
                       g.get("rationale"))
        for f in j.get("findings", []):
            b.add_finding(f["defect_id"], b.add_evidence_dicts(f.get("evidence", [])),
                          f.get("attribution", "undetermined"), f.get("confidence"),
                          f.get("repair_status", "not_repaired"), f.get("description"))
        for d in j.get("dimensions", []):
            b.set_dimension(d["dimension_id"], d.get("score"))
        oc = j.get("outcome") or {}
        b.set_outcome(oc.get("outcomes", []), b.add_evidence_dicts(oc.get("evidence", [])))
        if j.get("primary_attribution"):
            b.primary_attribution = AttributionClaim(target=j["primary_attribution"])
        conf = j.get("confidence")
        return None if conf is None else float(conf)
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise EvaluatorOutputError(f"malformed judgement: {exc}") from exc
