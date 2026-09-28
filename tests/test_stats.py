"""SD-26 intervals and bounds, SD-29 rate reporting, sign test."""

from __future__ import annotations

import math

from ignosis_eval.stats.intervals import sign_test_two_sided, wilson_interval, zero_failure_upper_bound
from ignosis_eval.stats.proportion import rate, whole_percent


def test_wilson_formula_z196():
    lo, hi = wilson_interval(9, 10)
    z, p, n = 1.96, 0.9, 10
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    assert (round(lo, 12), round(hi, 12)) == (round(c - h, 12), round(c + h, 12))
    assert round(lo, 3) == 0.596 and round(hi, 3) == 0.982


def test_interval_only_when_n_ge_10_and_zero_failure_bound():
    assert rate(5, 9)["wilson95_pct"] is None
    assert rate(5, 10)["wilson95_pct"] is not None
    r = rate(0, 15)
    assert r["zero_event_upper95_pct"] == round(100 * (1 - 0.05 ** (1 / 15))) == 18
    assert r["rule_of_three_approx_pct"] == 20
    assert rate(15, 15)["zero_nonevent_upper95_pct"] == 18
    assert abs(zero_failure_upper_bound(46) - (1 - 0.05 ** (1 / 46))) < 1e-15


def test_counts_first_and_whole_percent():
    r = rate(2, 3)
    assert r["kn"] == "2/3" and r["pct"] == 67
    assert whole_percent(1, 8) == 13 and whole_percent(1, 200) == 1 and whole_percent(0, 0) is None


def test_sign_test():
    assert sign_test_two_sided(0, 0) == 1.0
    assert sign_test_two_sided(5, 0) == 0.0625
    assert sign_test_two_sided(3, 3) == 1.0
