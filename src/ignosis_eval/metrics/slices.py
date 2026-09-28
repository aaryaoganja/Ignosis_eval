"""Sliced metrics: per input modality, per language (+ language delta), per split, per judge-bait flag.

Language delta (PROVISIONAL): for each core metric, delta = rate(language) - rate(reference language).
The reference is the configured language, else the language with the most item-reps (ties: sorted
first). Only point deltas are reported; no interval is attached to a difference (item-level pairing
and clustering make a naive difference interval misleading). Deltas are null when either side is
undefined, and are flagged when either side has n < policy.min_n.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from ignosis_eval.metrics.alignment import ItemRep
from ignosis_eval.metrics.definitions import compute
from ignosis_eval.stats.proportion import IntervalPolicy

CORE_METRICS = (
    "verdict_accuracy",
    "critical_misses",
    "major_recall",
    "unsupported_pass_verdict",
    "critical_false_positive_gate",
    "abstention_recall",
    "integrity_failures",
    "evidence_faithfulness",
    "modality_conformance",
)


def slice_by(irs: list[ItemRep], key: Callable[[ItemRep], str], policy: IntervalPolicy,
             metrics: tuple[str, ...] = CORE_METRICS) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[ItemRep]] = {}
    for ir in irs:
        groups.setdefault(key(ir), []).append(ir)
    return {g: {m: compute(m, sub, policy).to_dict() for m in metrics} for g, sub in sorted(groups.items())}


def language_delta(irs: list[ItemRep], policy: IntervalPolicy, reference: str | None = None,
                   metrics: tuple[str, ...] = CORE_METRICS) -> dict[str, Any]:
    per_lang = slice_by(irs, lambda ir: ir.language, policy, metrics)
    if not per_lang:
        return {"reference": None, "per_language": {}, "deltas": {}, "note": "no item-reps"}
    counts = Counter(ir.language for ir in irs)
    ref = reference if reference in per_lang else sorted(counts, key=lambda lang: (-counts[lang], lang))[0]
    note = None if reference in (None, ref) else f"configured reference {reference!r} absent; used {ref!r}"
    deltas: dict[str, dict[str, Any]] = {}
    for lang, res in per_lang.items():
        if lang == ref:
            continue
        deltas[lang] = {}
        for m in metrics:
            a, b = res[m], per_lang[ref][m]
            if a.get("rate") is None or b.get("rate") is None:
                deltas[lang][m] = {"delta": None, "note": "undefined on one side (n=0 or not measurable)"}
                continue
            small = (a["n"] or 0) < policy.min_n or (b["n"] or 0) < policy.min_n
            deltas[lang][m] = {
                "delta": a["rate"] - b["rate"],
                "delta_pct_points": round(100 * (a["rate"] - b["rate"]), 4),
                "n_language": a["n"], "n_reference": b["n"],
                "note": f"n below min_n={policy.min_n}: descriptive only" if small else None,
            }
    return {"reference": ref, "per_language": per_lang, "deltas": deltas, "note": note,
            "definition": "delta = rate(language) - rate(reference); point estimate only, no interval"}
