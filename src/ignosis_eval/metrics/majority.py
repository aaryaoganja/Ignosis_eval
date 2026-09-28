"""Majority output — scoring-spec SD-04 / SD-05 / SD-18.

k = 5 reps, "majority" = at least 3 of 5. For other k (tuning runs may use k = 1) the threshold is the
strict majority floor(k/2) + 1; this is identical to 3 when k = 5.

Gate / code majority status: the modal status over the OK reps. When statuses tie for the mode, the
tied statuses are re-ranked with each EVALUATION_FAILED rep counted as INCONCLUSIVE ("contributes the
value INCONCLUSIVE only to tie-breaking counts"), and any remaining tie is broken by precedence
FAIL > INCONCLUSIVE > OUT_OF_SCOPE > NA > PASS (codes: DEFECT > ...). With no OK rep the majority
status is INCONCLUSIVE. EVALUATION_FAILED is never counted as FAIL.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ignosis_eval.metrics.alignment import (
    CODE_PRECEDENCE,
    DW_PRECEDENCE,
    EVALUATION_FAILED,
    GATE_PRECEDENCE,
    VERDICT_PRECEDENCE,
    RepObs,
)


def threshold(k: int) -> int:
    return k // 2 + 1


def _modal(values: list[str], precedence: Sequence[str], n_failed: int = 0) -> str:
    counts = Counter(values)
    if not counts:
        return "INCONCLUSIVE"
    top = max(counts.values())
    tied = [s for s in counts if counts[s] == top]
    if len(tied) > 1 and n_failed:
        tb = {s: counts[s] + (n_failed if s == "INCONCLUSIVE" else 0) for s in tied}
        best = max(tb.values())
        tied = [s for s in tied if tb[s] == best]
    return min(tied, key=precedence.index)


def gate_majority_status(reps: Sequence[RepObs], gate: str) -> str:
    return _modal([r.gates[gate].status for r in reps if not r.failed], GATE_PRECEDENCE,
                  sum(r.failed for r in reps))


def code_majority_status(reps: Sequence[RepObs], code: str) -> str:
    return _modal([r.code_status(code) for r in reps if not r.failed], CODE_PRECEDENCE,  # type: ignore[misc]
                  sum(r.failed for r in reps))


def verdict_majority(reps: Sequence[RepObs]) -> str:
    """Modal verdict over all reps, EVALUATION_FAILED counted as its own value."""
    return _modal([r.verdict for r in reps], VERDICT_PRECEDENCE)


def detected_count(reps: Sequence[RepObs], gate: str) -> int:
    """SD-05: detected = fired; EVALUATION_FAILED => not detected."""
    return sum(gate in r.fired for r in reps)


def confirmed_count(reps: Sequence[RepObs], gate: str) -> int:
    return sum(gate in r.fired and r.gates[gate].critical_status == "CONFIRMED" for r in reps)


def suspected_count(reps: Sequence[RepObs], gate: str) -> int:
    return sum(gate in r.fired and r.gates[gate].critical_status == "SUSPECTED" for r in reps)


def emitted_count(reps: Sequence[RepObs], code: str) -> int:
    return sum(r.emitted(code) for r in reps)


def dw_majority(reps: Sequence[RepObs]) -> str | None:
    values = [r.dangerous_win for r in reps if not r.failed and r.dangerous_win is not None]
    return _modal(values, DW_PRECEDENCE) if values else None


@dataclass(frozen=True)
class GateMajority:
    status: str
    detected: int
    fired: bool
    confirmed: int
    suspected: int
    critical_status: str | None  # given majority fired: CONFIRMED if CONFIRMED in >= threshold reps


@dataclass
class UnitAgg:
    """Majority output of one system on one unit."""

    reps: list[RepObs]
    gate_ids: tuple[str, ...]

    @property
    def k(self) -> int:
        return len(self.reps)

    @property
    def thr(self) -> int:
        return threshold(self.k)

    def gate(self, g: str) -> GateMajority:
        det, conf = detected_count(self.reps, g), confirmed_count(self.reps, g)
        fired = det >= self.thr
        return GateMajority(gate_majority_status(self.reps, g), det, fired, conf, suspected_count(self.reps, g),
                            ("CONFIRMED" if conf >= self.thr else "SUSPECTED") if fired else None)

    def fired_set(self) -> frozenset[str]:
        return frozenset(g for g in self.gate_ids if detected_count(self.reps, g) >= self.thr)

    def code_status(self, c: str) -> str:
        return code_majority_status(self.reps, c)

    def code_emitted(self, c: str) -> bool:
        return emitted_count(self.reps, c) >= self.thr

    @property
    def verdict(self) -> str:
        return verdict_majority(self.reps)

    @property
    def n_failed(self) -> int:
        return sum(r.failed for r in self.reps)

    def consistent(self) -> bool:
        """SD-19: same verdict value (EVALUATION_FAILED counts) and the same fired-gate set in every rep."""
        return len({r.verdict for r in self.reps}) == 1 and len({r.fired for r in self.reps}) == 1

    def reps_agreeing_with_modal_verdict(self) -> int:
        v = self.verdict
        return sum(r.verdict == v for r in self.reps)

    def clean_loss(self) -> bool:
        return sum(bool(r.clean_loss) for r in self.reps if not r.failed) >= self.thr

    def within_scope_complete(self) -> bool:
        return sum(bool(r.within_scope_complete) for r in self.reps if not r.failed) >= self.thr


__all__ = ["EVALUATION_FAILED", "GateMajority", "UnitAgg", "threshold"]
