"""Binomial confidence intervals: Wilson score and Clopper-Pearson (exact).

Pure Python (no SciPy). Clopper-Pearson bounds are found by bisection on exact binomial tail
probabilities computed in log space, so they are exact to ~1e-12 for any n used here.

  Wilson:           center +/- half-width of the score interval (good coverage for moderate n).
  Clopper-Pearson:  lower = p such that P(X >= k | n, p) = alpha/2   (0 if k == 0)
                    upper = p such that P(X <= k | n, p) = alpha/2   (1 if k == n)
                    Conservative; preferred for rare safety events (e.g. 0 critical misses out of n).
"""

from __future__ import annotations

import math
from statistics import NormalDist


def _check(k: int, n: int, confidence: float) -> None:
    if n <= 0:
        raise ValueError("interval undefined for n <= 0")
    if not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n, got k={k} n={n}")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")


def z_value(confidence: float) -> float:
    return NormalDist().inv_cdf(1 - (1 - confidence) / 2)


def wilson_interval(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    _check(k, n, confidence)
    z = z_value(confidence)
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    lo = 0.0 if k == 0 else max(0.0, center - half)
    hi = 1.0 if k == n else min(1.0, center + half)
    return lo, hi


def _log_pmf(i: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if i == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if i == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(p) + (n - i) * math.log1p(-p))


def _logsumexp(xs: list[float]) -> float:
    m = max(xs)
    if m == -math.inf:
        return -math.inf
    return m + math.log(sum(math.exp(x - m) for x in xs))


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return min(1.0, math.exp(_logsumexp([_log_pmf(i, n, p) for i in range(k + 1)])))


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return min(1.0, math.exp(_logsumexp([_log_pmf(i, n, p) for i in range(k, n + 1)])))


def _bisect(f, target: float, increasing: bool, tol: float = 1e-13) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        v = f(mid)
        if (v < target) == increasing:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def clopper_pearson_interval(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    _check(k, n, confidence)
    a2 = (1 - confidence) / 2
    lo = 0.0 if k == 0 else _bisect(lambda p: binom_sf(k, n, p), a2, increasing=True)
    hi = 1.0 if k == n else _bisect(lambda p: binom_cdf(k, n, p), a2, increasing=False)
    return lo, hi


METHODS = {"wilson": wilson_interval, "clopper_pearson": clopper_pearson_interval}
