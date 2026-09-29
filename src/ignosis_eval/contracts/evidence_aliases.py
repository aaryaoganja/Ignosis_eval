"""Canonical evidence-element names for the frozen DEV blueprint (BD-05, docs/bd-changelog.md).

The rubric 1.2 `required_evidence_elements` names are canonical. The frozen blueprint uses two outdated aliases and
one non-element-specific requirement; they are mapped here, once, for the design validator and the scorer.
No new rubric element is created.
"""

from __future__ import annotations

from dataclasses import dataclass

# (check id, blueprint name) -> canonical rubric element name
ELEMENT_ALIASES: dict[tuple[str, str], str] = {
    ("G5", "continued_collection_turns"): "continued_collection_turns_or_refusal_turn",
    ("POL-01", "window_end_turn"): "window_or_statement_turn",
}


@dataclass(frozen=True)
class NonElementEvidence:
    """Evidence the blueprint cites that is not a rubric element. `absent_element` is the rubric element that has no
    turn in this case, so SD-14 neither requires it nor counts it as missing gold."""

    check: str
    name: str
    absent_element: str
    basis: str


NON_ELEMENT_EVIDENCE: dict[tuple[str, str], NonElementEvidence] = {
    ("G5", "closing_turn"): NonElementEvidence(
        "G5", "closing_turn", "continued_collection_turns_or_refusal_turn",
        "BD-05: G5 decision_table order 7 (no honoring event, no collection turn, no refusal)"),
}


def canonical_element(check_id: str, name: str) -> str:
    return ELEMENT_ALIASES.get((check_id, name), name)


def canonical_elements(check_id: str, elements: dict[str, int]) -> tuple[dict[str, int], set[str]]:
    """(elements under canonical names without non-element evidence, rubric elements marked absent by it)."""
    out: dict[str, int] = {}
    absent: set[str] = set()
    for name, turn in elements.items():
        ne = NON_ELEMENT_EVIDENCE.get((check_id, name))
        if ne is not None:
            absent.add(ne.absent_element)
            continue
        out.setdefault(canonical_element(check_id, name), turn)
    return out, absent


__all__ = ["ELEMENT_ALIASES", "NON_ELEMENT_EVIDENCE", "NonElementEvidence", "canonical_element",
           "canonical_elements"]
