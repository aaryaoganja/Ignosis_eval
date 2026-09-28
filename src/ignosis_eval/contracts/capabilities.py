"""Capability derivation: what a (normalized) canonical input makes observable.

Used by evaluators (to know what they may assert) and by the scorer (modality conformance). Both use the
same declared contract; neither depends on the other.
"""

from __future__ import annotations

from collections.abc import Iterable

from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.enums import Capability


def available_capabilities(inp: CanonicalInput) -> frozenset[Capability]:
    ea = inp.evidence_availability
    caps: set[Capability] = set()
    if inp.transcript is not None and inp.transcript.turns:
        caps.add(Capability.CONTENT)
    if inp.audio is not None:
        caps.add(Capability.AUDIO)
    if ea.turn_timestamps:
        caps.add(Capability.TURN_TIMESTAMPS)
    if ea.speaker_labels and inp.transcript is not None and inp.transcript.turns:
        caps.add(Capability.SPEAKER_LABELS)
    if inp.call_start_ts is not None:
        caps.add(Capability.CALL_START_TS)
    if ea.call_start_captured:
        caps.add(Capability.CALL_START_CAPTURED)
    return frozenset(caps)


def missing_capabilities(required: Iterable[Capability], available: frozenset[Capability]) -> list[Capability]:
    return [c for c in required if c not in available]
