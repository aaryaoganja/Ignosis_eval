"""Intervals and bounds — scoring-spec SD-26 (normative), plus the exact sign test of SD-29 / P-15.

* Wilson 95% with z = 1.96 (fixed by the spec, not derived from a confidence level), shown only when
  n >= 10: (p + z²/2n ± z·sqrt(p(1−p)/n + z²/4n²)) / (1 + z²/n).
* Zero-failure one-sided 95% upper bound = 1 − 0.05^(1/n). The rule of three (3/n) may be shown only as
  an approximation label.
* Exact two-sided sign test on discordant counts (reported, never decisive).
"""

from __future__ import annotations

import math

WILSON_Z = 1.96
WILSON_MIN_N = 10


def wilson_interval(k: int, n: int) -> tuple[float, float]:
    if n <= 0 or not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n and n > 0, got k={k} n={n}")
    z, p = WILSON_Z, k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def zero_failure_upper_bound(n: int) -> float:
    """One-sided 95% upper bound on a rate after observing 0 events in n trials."""
    if n <= 0:
        raise ValueError("n must be positive")
    return 1 - 0.05 ** (1 / n)


def rule_of_three(n: int) -> float:
    """Approximation label only (SD-26); never used as the bound."""
    return 3 / n


def sign_test_two_sided(b: int, c: int) -> float:
    """Exact two-sided sign test for b vs c discordant pairs (p = 0.5). Returns 1.0 when b + c = 0."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)
