"""Evidence verification of B's extraction output (rubric.yaml 1.2-mvp › extraction_schema, AJ-07).

  * an event is dropped when its quote fails SD-13 faithfulness against its turn, or when the turn's speaker does not
    match the event side (event_common.turn: "the speaker of the turn must match the event side (borrower/agent)");
  * a `responds_to` id is removed when it does not reference a BORROWER event with an earlier turn, or when the
    event carrying it is not one of `responds_to.carried_by` ("invalid ids are removed by the verifier and logged");
  * identity checks (borrower replies) and stated values (agent turns) with unverifiable quotes are dropped; a
    `component_of` pointing at a dropped value is cleared, and a correction whose values were dropped is dropped.
Every drop is logged; nothing is repaired or guessed.
"""

from __future__ import annotations

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import Role
from ignosis_eval.contracts.evaluation_record import Evidence
from ignosis_eval.contracts.extraction import RESPONDS_TO_CARRIED_BY, EventSource, ExtractionOutput
from ignosis_eval.engine.finalize import DerivationLog, verify_evidence
from ignosis_eval.spec.loader import Spec


def verify_extraction(ex: ExtractionOutput, ni: NormalizedInput, spec: Spec,
                      log: DerivationLog | None = None) -> ExtractionOutput:
    log = log if log is not None else DerivationLog()
    threshold = float(spec.threshold("quote_match_min"))

    def ok(turn: int, quote: str, role: Role, source: EventSource | None = None) -> bool:
        return verify_evidence(Evidence(turn=turn, quote=quote, role=role, source=source), ni, threshold)[0]

    events = []
    for e in ex.events:
        if not ok(e.turn, e.quote, e.side, e.source):
            log.add("extraction-verifier", e.event_id, "dropped event: unverifiable quote or turn speaker does not "
                    "match the event side", type=e.type)
            continue
        events.append(e)
    by_id = {e.event_id: e for e in events}
    cleaned = []
    for e in events:
        if e.responds_to:
            keep: list[str] = []
            drop: list[str] = []
            for rid in e.responds_to:
                target = by_id.get(rid)
                valid = (e.type in RESPONDS_TO_CARRIED_BY and target is not None and target.side is Role.BORROWER
                         and target.turn < e.turn)
                (keep if valid else drop).append(rid)
            if drop:
                log.add("extraction-verifier", e.event_id, "removed invalid responds_to ids", ids=drop, type=e.type)
                e = e.model_copy(update={"responds_to": keep})
        cleaned.append(e)
    checks = [c for c in ex.call_frame.identity_checks if ok(c.turn, c.quote, Role.BORROWER)]
    for c in ex.call_frame.identity_checks:
        if c not in checks:
            log.add("extraction-verifier", f"identity_check@{c.turn}", "dropped identity check with unverifiable quote")
    values = [v for v in ex.agent_stated_values if ok(v.turn, v.quote, Role.AGENT)]
    kept_values = {v.value_id for v in values}
    for v in ex.agent_stated_values:
        if v.value_id not in kept_values:
            log.add("extraction-verifier", v.value_id, "dropped stated value with unverifiable quote")
    values = [v if v.component_of is None or v.component_of in kept_values else v.model_copy(update={"component_of": None})
              for v in values]
    final = []
    for e in cleaned:
        if e.type == "correction" and not ({e.corrects, e.new_value_id} <= kept_values):
            log.add("extraction-verifier", e.event_id, "dropped correction: a stated value it references was dropped")
            continue
        final.append(e)
    frame = ex.call_frame.model_copy(update={"identity_checks": checks})
    return ex.model_copy(update={"events": final, "call_frame": frame, "agent_stated_values": values})
