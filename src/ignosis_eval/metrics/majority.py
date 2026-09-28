"""Majority output — scoring-spec SD-04 (AJ-11) / SD-05 / SD-18. No modal status and no tie-breaking.

Every majority quantity is a per-rep boolean indicator that must be true in >= 3 of 5 reps. An EVALUATION_FAILED
rep sets every gate and code indicator to false (it is neither a detection, a pass nor an abstention); for the
verdict only, EVALUATION_FAILED is a value. When no label reaches the threshold the majority is NO_MAJORITY,
which is never counted as correct. For k != 5 (tuning runs may use k = 1) the threshold is floor(k/2) + 1, which
is 3 for k = 5 (convention, docs/spec-reconciliation.md §3).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from ignosis_eval.metrics.alignment import EVALUATION_FAILED, RepObs

NO_MAJORITY = "NO_MAJORITY"
GATE_STATUSES = ("PASS", "NA", "INCONCLUSIVE", "OUT_OF_SCOPE")  # FAIL is decided by the fired indicator
CODE_STATUSES = ("PASS", "NA", "INCONCLUSIVE", "OUT_OF_SCOPE")  # DEFECT is decided by the emitted indicator


def threshold(k: int) -> int:
    return k // 2 + 1


def count(reps: Sequence[RepObs], indicator: Callable[[RepObs], bool]) -> int:
    """Number of reps where the indicator holds; an EVALUATION_FAILED rep contributes false."""
    return sum(1 for r in reps if not r.failed and indicator(r))


def holds(reps: Sequence[RepObs], indicator: Callable[[RepObs], bool]) -> bool:
    return count(reps, indicator) >= threshold(len(reps))


def gate_status_in(reps: Sequence[RepObs], gate: str, statuses: Iterable[str]) -> bool:
    """SD-04 status-set indicator: the gate status is in `statuses` in >= 3 reps."""
    s = set(statuses)
    return holds(reps, lambda r: r.gates[gate].status in s)


def code_status_in(reps: Sequence[RepObs], code: str, statuses: Iterable[str]) -> bool:
    s = set(statuses)
    return holds(reps, lambda r: r.code_status(code) in s)


def gate_majority_status(reps: Sequence[RepObs], gate: str) -> str:
    if holds(reps, lambda r: gate in r.fired):
        return "FAIL"
    for st in GATE_STATUSES:
        if holds(reps, lambda r, st=st: r.gates[gate].status == st):  # type: ignore[misc]
            return st
    return NO_MAJORITY


def code_majority_status(reps: Sequence[RepObs], code: str) -> str:
    if holds(reps, lambda r: r.emitted(code)):
        return "DEFECT"
    for st in CODE_STATUSES:
        if holds(reps, lambda r, st=st: r.code_status(code) == st):  # type: ignore[misc]
            return st
    return NO_MAJORITY


def verdict_majority(reps: Sequence[RepObs]) -> str:
    """The verdict value held in >= 3 reps (EVALUATION_FAILED is a value here), else NO_MAJORITY."""
    counts = Counter(r.verdict for r in reps)
    best = [v for v, n in counts.items() if n >= threshold(len(reps))]
    return best[0] if best else NO_MAJORITY


def most_frequent_verdict_count(reps: Sequence[RepObs]) -> int:
    """SD-19 distribution: how many reps hold the most frequent verdict value (a count; no tie-break needed)."""
    return max(Counter(r.verdict for r in reps).values(), default=0)


def detected_count(reps: Sequence[RepObs], gate: str) -> int:
    """SD-05: detected = fired; EVALUATION_FAILED => not detected."""
    return count(reps, lambda r: gate in r.fired)


def confirmed_count(reps: Sequence[RepObs], gate: str) -> int:
    return count(reps, lambda r: gate in r.fired and r.gates[gate].critical_status == "CONFIRMED")


def suspected_count(reps: Sequence[RepObs], gate: str) -> int:
    return count(reps, lambda r: gate in r.fired and r.gates[gate].critical_status == "SUSPECTED")


def emitted_count(reps: Sequence[RepObs], code: str) -> int:
    return count(reps, lambda r: r.emitted(code))


def dw_majority(reps: Sequence[RepObs]) -> str:
    """SD-18: the DW value held in >= 3 reps, else NO_MAJORITY."""
    for value in ("CRITICAL", "MATERIAL", "NONE"):
        if holds(reps, lambda r, v=value: r.dangerous_win == v):  # type: ignore[misc]
            return value
    return NO_MAJORITY


@dataclass(frozen=True)
class GateMajority:
    status: str  # FAIL | PASS | NA | INCONCLUSIVE | OUT_OF_SCOPE | NO_MAJORITY
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

    def gate_in(self, g: str, statuses: Iterable[str]) -> bool:
        return gate_status_in(self.reps, g, statuses)

    def code_in(self, c: str, statuses: Iterable[str]) -> bool:
        return code_status_in(self.reps, c, statuses)

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

    def reps_holding_most_frequent_verdict(self) -> int:
        return most_frequent_verdict_count(self.reps)

    def clean_loss(self) -> bool:
        return holds(self.reps, lambda r: bool(r.clean_loss))

    def within_scope_complete(self) -> bool:
        return holds(self.reps, lambda r: bool(r.within_scope_complete))


__all__ = ["EVALUATION_FAILED", "NO_MAJORITY", "GateMajority", "UnitAgg", "threshold"]
