"""TRT-06 measurement (AJ-02): duration when the turn has timestamps, otherwise word count, in every mode."""

from __future__ import annotations

from ignosis_eval.contracts.canonical_input import NormalizedInput, Turn
from ignosis_eval.contracts.enums import MeasurementBasis
from ignosis_eval.spec.loader import Spec


def trt06_measure(turn: Turn, ni: NormalizedInput, spec: Spec) -> tuple[MeasurementBasis, bool]:
    """(measurement_basis, exceeds the monologue threshold) for one agent turn."""
    if turn.start_s is not None and turn.end_s is not None:
        return MeasurementBasis.DURATION, (turn.end_s - turn.start_s) > float(spec.threshold("monologue_max_seconds"))
    return MeasurementBasis.WORD_COUNT, len(turn.text.split()) > int(spec.threshold("monologue_max_words"))
