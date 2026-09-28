"""Rate reporting — scoring-spec SD-26 / SD-29: counts first (always k/n), whole-percent rounding, a
Wilson 95% interval only when n >= 10, and the zero-failure upper bound when no event was observed.

Many SD metrics pool units over reps, or several checks per item, so their units are not independent.
SD-26 does not restrict the interval to independent units; the interval is shown as specified and the
`clustered` flag carries the caveat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ignosis_eval.stats.intervals import WILSON_MIN_N, rule_of_three, wilson_interval, zero_failure_upper_bound


def whole_percent(k: int, n: int) -> int | None:
    """Round half up to a whole percent (deterministic; avoids banker's rounding)."""
    if n == 0:
        return None
    return int((200 * k + n) // (2 * n))


@dataclass(frozen=True)
class Rate:
    k: int
    n: int
    clustered: bool = False  # pooled over reps / several checks per item

    def __post_init__(self) -> None:
        if self.n < 0 or not 0 <= self.k <= max(self.n, 0):
            raise ValueError(f"invalid rate k={self.k} n={self.n}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"k": self.k, "n": self.n, "kn": f"{self.k}/{self.n}",
                               "pct": whole_percent(self.k, self.n), "clustered": self.clustered,
                               "wilson95_pct": None, "zero_event_upper95_pct": None,
                               "zero_nonevent_upper95_pct": None}
        if self.n >= WILSON_MIN_N:
            lo, hi = wilson_interval(self.k, self.n)
            out["wilson95_pct"] = [round(100 * lo), round(100 * hi)]
        if self.n > 0 and self.k == 0:
            out["zero_event_upper95_pct"] = round(100 * zero_failure_upper_bound(self.n))
            out["rule_of_three_approx_pct"] = round(100 * rule_of_three(self.n))
        if self.n > 0 and self.k == self.n:  # e.g. 0 misses: bound on the complementary (failure) rate
            out["zero_nonevent_upper95_pct"] = round(100 * zero_failure_upper_bound(self.n))
        return out


def rate(k: int, n: int, *, clustered: bool = False) -> dict[str, Any]:
    return Rate(k, n, clustered).to_dict()
