"""Proportion results (k/n + percentage + honest interval reporting).

An interval is reported only when ALL of these hold; otherwise `interval` is null and
`interval_note` says why (no interval is ever fabricated):
  * n > 0
  * n >= policy.min_n
  * the units are independent: one unit per item and a single repetition. Item x repetition units are
    clustered by item, and several defects/evidence items per item are clustered too; binomial
    intervals would overstate precision. For such metrics the scorer reports per-repetition results
    (each over independent items) instead.

PROVISIONAL: min_n and the metric->method assignment must be reconciled with the Stage 4 spec.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from ignosis_eval.stats.intervals import METHODS

Method = Literal["wilson", "clopper_pearson"]


@dataclass(frozen=True)
class IntervalPolicy:
    confidence: float = 0.95
    min_n: int = 10
    default_method: Method = "wilson"
    safety_method: Method = "clopper_pearson"

    def to_dict(self) -> dict[str, Any]:
        return {"confidence": self.confidence, "min_n": self.min_n, "default_method": self.default_method,
                "safety_method": self.safety_method}


def units_independent(unit_items: Iterable[str], reps: Iterable[int]) -> bool:
    """True iff every unit comes from a distinct item and all units come from a single repetition."""
    counts = Counter(unit_items)
    return len(set(reps)) <= 1 and all(c == 1 for c in counts.values())


@dataclass
class Proportion:
    k: int
    n: int
    unit: str
    independent: bool
    safety: bool = False
    policy: IntervalPolicy = field(default_factory=IntervalPolicy)

    def __post_init__(self) -> None:
        if self.n < 0 or not 0 <= self.k <= max(self.n, 0):
            raise ValueError(f"invalid proportion k={self.k} n={self.n}")

    @property
    def rate(self) -> float | None:
        return None if self.n == 0 else self.k / self.n

    @property
    def pct(self) -> float | None:
        r = self.rate
        return None if r is None else round(100.0 * r, 4)

    @property
    def method(self) -> Method:
        return self.policy.safety_method if self.safety else self.policy.default_method

    def interval(self) -> tuple[dict[str, Any] | None, str | None]:
        if self.n == 0:
            return None, "no eligible units (n=0)"
        if not self.independent:
            return None, "units are not independent (clustered by item and/or repetition); see per-repetition results"
        if self.n < self.policy.min_n:
            return None, f"n={self.n} is below min_n={self.policy.min_n}; interval not reported"
        lo, hi = METHODS[self.method](self.k, self.n, self.policy.confidence)
        return {"method": self.method, "confidence": self.policy.confidence, "low": lo, "high": hi,
                "low_pct": round(100 * lo, 4), "high_pct": round(100 * hi, 4)}, None

    def to_dict(self) -> dict[str, Any]:
        iv, note = self.interval()
        return {"k": self.k, "n": self.n, "rate": self.rate, "pct": self.pct, "unit": self.unit,
                "independent_units": self.independent, "interval": iv, "interval_note": note}
