"""Metric registry — every reported metric points at its normative definition in docs/spec/scoring-spec.md.

A change to any definition bumps METRIC_DEFINITIONS_VERSION (versions.py), updates the SD-31 fixture tests
in tests/test_scoring_spec.py, and needs a spec change first: the definitions are frozen.
"""

from __future__ import annotations

from ignosis_eval.versions import METRIC_DEFINITIONS_VERSION

METRICS: dict[str, tuple[str, str]] = {
    "sd06": ("SD-06", "gate outcome mapping table, per rep and on majority output"),
    "sd07": ("SD-07", "critical recall (pooled per rep), stability, majority-output critical misses, flip-to-pass"),
    "sd08": ("SD-08", "targeted false fires on controls (primary), confirmed-only, global false fires"),
    "sd09": ("SD-09", "unsupported pass (H4), overclaim, unsupported defect, unit/check-level over-abstention"),
    "sd10": ("SD-10", "abstention recall and precision"),
    "sd11": ("SD-11", "external-truth assertions (H1): structural violations + confirmed textual candidates"),
    "sd12": ("SD-12", "finding matching (anchor ±1), per-code and Major/Minor micro precision/recall"),
    "sd13": ("SD-13", "quote faithfulness; H2 violations"),
    "sd14": ("SD-14", "evidence completeness"),
    "sd15": ("SD-15", "evidence support (human, rep 1)"),
    "sd16": ("SD-16", "attribution accuracy and unjustified attribution"),
    "sd17": ("SD-17", "verdict accuracy, lenient/strict errors, S3"),
    "sd18": ("SD-18", "Dangerous Win / Clean Loss accuracy"),
    "sd19": ("SD-19", "consistency"),
    "sd20": ("SD-20", "pair accuracy, inversion (H5), collateral change"),
    "sd21": ("SD-21", "twin agreement"),
    "sd22": ("SD-22", "snippet accuracy (component tests)"),
    "sd23": ("SD-23", "capability violations (H6) and per-mode metrics"),
    "sd24": ("SD-24", "latency p50/p95 (nearest rank)"),
    "sd25": ("SD-25", "cost"),
    "hard_requirements": ("SD-27", "H1–H7"),
    "tiers": ("SD-30", "safety tiers S0–S11 (lexicographic)"),
}

__all__ = ["METRICS", "METRIC_DEFINITIONS_VERSION"]
