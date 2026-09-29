"""Import boundaries (CLAUDE.md invariants 1 and 3; experiment-protocol P-12).

Evaluator side = evaluators, engine, pipeline. Scorer side = scoring, metrics, golddrv, stats. Neither side
imports the other; evaluators never see gold, gold freeze, benchmark authoring, registries tooling or the runner.
Checked statically (AST, per file) and transitively (a fresh interpreter's sys.modules after import).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "src" / "ignosis_eval"
SUPERSEDED = ("ignosis_eval.evaluators.heuristics",)
EVALUATOR_FORBIDDEN = ["ignosis_eval.contracts.gold_label", "ignosis_eval.contracts.case_card",
                       "ignosis_eval.integrity.freeze", "ignosis_eval.benchmark", "ignosis_eval.scoring",
                       "ignosis_eval.metrics", "ignosis_eval.golddrv", "ignosis_eval.runner"]
SCORER_FORBIDDEN = ["ignosis_eval.evaluators", "ignosis_eval.engine", "ignosis_eval.pipeline", "ignosis_eval.runner",
                    "ignosis_eval.benchmark.checks", "ignosis_eval.benchmark.case_card_rules"]
FORBIDDEN = {
    "contracts": ["ignosis_eval.integrity", "ignosis_eval.benchmark", "ignosis_eval.stats", "ignosis_eval.metrics",
                  "ignosis_eval.scoring", "ignosis_eval.evaluators", "ignosis_eval.runner", "ignosis_eval.engine",
                  "ignosis_eval.pipeline", "ignosis_eval.golddrv"],
    "evaluators": EVALUATOR_FORBIDDEN, "engine": EVALUATOR_FORBIDDEN, "pipeline": EVALUATOR_FORBIDDEN,
    "scoring": SCORER_FORBIDDEN, "metrics": SCORER_FORBIDDEN, "golddrv": SCORER_FORBIDDEN, "stats": SCORER_FORBIDDEN,
    "devbaseline": SCORER_FORBIDDEN,  # DEV draft baseline: scorer side (reads records, never the evaluator)
    "spec": ["ignosis_eval.evaluators", "ignosis_eval.engine", "ignosis_eval.pipeline", "ignosis_eval.scoring",
             "ignosis_eval.metrics", "ignosis_eval.golddrv", "ignosis_eval.runner", "ignosis_eval.contracts.gold_label"],
}


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def _live_files():
    return [p for p in PKG.rglob("*.py") if p.name != "heuristics.py"]


@pytest.mark.parametrize("subpkg", sorted(FORBIDDEN))
def test_static_import_boundaries(subpkg):
    offenders = []
    for py in (PKG / subpkg).rglob("*.py"):
        for imp in _imports(py):
            if any(imp == b or imp.startswith(b + ".") for b in FORBIDDEN[subpkg]):
                offenders.append(f"{py.relative_to(PKG)} imports {imp}")
    assert not offenders, "\n".join(offenders)


def _loaded_after(modules: list[str]) -> list[str]:
    code = ("import json, sys\n" + "".join(f"import {m}\n" for m in modules)
            + "print(json.dumps(sorted(m for m in sys.modules if m.startswith('ignosis_eval'))))")
    out = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True, cwd=REPO)
    return json.loads(out.stdout)


def test_transitive_evaluator_side_never_loads_gold_or_scorer():
    loaded = _loaded_after(["ignosis_eval.evaluators.registry", "ignosis_eval.evaluators.pipelines",
                            "ignosis_eval.evaluators.k0", "ignosis_eval.engine.finalize",
                            "ignosis_eval.pipeline.normalize"])
    bad = [m for m in loaded if any(m == b or m.startswith(b + ".") for b in EVALUATOR_FORBIDDEN)]
    assert not bad, bad


def test_transitive_scorer_side_never_loads_evaluator_code():
    loaded = _loaded_after(["ignosis_eval.scoring.scorer", "ignosis_eval.golddrv.derive", "ignosis_eval.metrics.compute",
                            "ignosis_eval.stats.proportion"])
    bad = [m for m in loaded if any(m == b or m.startswith(b + ".") for b in SCORER_FORBIDDEN)]
    assert not bad, bad


def test_no_live_module_uses_superseded_files():
    offenders = []
    for py in _live_files():
        text = py.read_text(encoding="utf-8")
        if any(s in _imports(py) or s.replace(".", "/") in text for s in SUPERSEDED):
            offenders.append(str(py.relative_to(PKG)))
        if "collections_placeholder.yaml" in text or "benchmark_smoke" in text:
            offenders.append(str(py.relative_to(PKG)))
    assert not offenders, offenders
