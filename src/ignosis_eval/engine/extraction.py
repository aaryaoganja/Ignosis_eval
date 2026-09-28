"""Evidence verification of B's extraction output (AJ-07, FP-08).

  * an event whose quote fails SD-13 faithfulness (turn exists, role matches, score >= quote_match_min) is dropped;
  * a `responds_to` id is dropped when it does not name an EARLIER borrower event, or when the event carrying it is
    not one of the types allowed to respond (extraction_schema.responds_to.allowed_on);
  * identity checks and stated values with unverifiable quotes are dropped.
Every drop is logged; nothing is repaired or guessed.
"""

from __future__ import annotations

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import Role
from ignosis_eval.contracts.evaluation_record import Evidence
from ignosis_eval.contracts.extraction import RESPONDS_TO_ALLOWED_ON, ExtractionOutput
from ignosis_eval.engine.finalize import DerivationLog, verify_evidence
from ignosis_eval.spec.loader import Spec


def verify_extraction(ex: ExtractionOutput, ni: NormalizedInput, spec: Spec,
                      log: DerivationLog | None = None) -> ExtractionOutput:
    log = log if log is not None else DerivationLog()
    threshold = float(spec.threshold("quote_match_min"))
    borrower_types = set(spec.rubric["extraction_vocabulary"]["borrower_event_types"])

    def ok(turn: int, quote: str, role: Role) -> bool:
        return verify_evidence(Evidence(turn=turn, quote=quote, role=role), ni, threshold)[0]

    events = []
    for e in ex.events:
        if not ok(e.turn, e.quote, e.role):
            log.add("extraction-verifier", e.id, "dropped event with unverifiable quote", type=e.type)
            continue
        events.append(e)
    by_id = {e.id: e for e in events}
    cleaned = []
    for e in events:
        if e.responds_to:
            keep: list[str] = []
            drop: list[str] = []
            for rid in e.responds_to:
                target = by_id.get(rid)
                valid = (e.type in RESPONDS_TO_ALLOWED_ON and target is not None and target.type in borrower_types
                         and target.turn < e.turn)
                (keep if valid else drop).append(rid)
            if drop:
                log.add("extraction-verifier", e.id, "dropped invalid responds_to ids", ids=drop, type=e.type)
                e = e.model_copy(update={"responds_to": keep})
        cleaned.append(e)
    checks = [c for c in ex.identity_checks if ok(c.turn, c.quote, Role.BORROWER)]
    values = [v for v in ex.agent_stated_values if ok(v.turn, v.quote, Role.AGENT)]
    for dropped in ({c.id for c in ex.identity_checks} - {c.id for c in checks}) | \
            ({v.id for v in ex.agent_stated_values} - {v.id for v in values}):
        log.add("extraction-verifier", dropped, "dropped item with unverifiable quote")
    kept_values = {v.id for v in values}
    values = [v if v.component_of is None or v.component_of in kept_values else v.model_copy(update={"component_of": None})
              for v in values]
    final = []
    for e in cleaned:
        if e.type == "correction":
            refs = [r for r in e.corrects or [] if r in kept_values]
            if not refs:
                log.add("extraction-verifier", e.id, "dropped correction: its stated values were dropped")
                continue
            nv = e.new_value_id if e.new_value_id in kept_values else None
            e = e.model_copy(update={"corrects": refs, "new_value_id": nv})
        final.append(e)
    return ex.model_copy(update={"events": final, "identity_checks": checks, "agent_stated_values": values})
