"""Static import-boundary checks.

The scorer must be independent of the evaluator: scoring/metrics may not import evaluators or runner.
Evaluators may not import gold-label contracts, gold-freeze code, scoring or metrics.
Contracts are the foundation and import no other ignosis_eval subpackage.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "ignosis_eval"

FORBIDDEN = {
    "contracts": ["ignosis_eval.integrity", "ignosis_eval.benchmark", "ignosis_eval.stats", "ignosis_eval.metrics",
                  "ignosis_eval.scoring", "ignosis_eval.evaluators", "ignosis_eval.runner"],
    "stats": ["ignosis_eval.evaluators", "ignosis_eval.runner", "ignosis_eval.scoring", "ignosis_eval.metrics"],
    "metrics": ["ignosis_eval.evaluators", "ignosis_eval.runner"],
    "scoring": ["ignosis_eval.evaluators", "ignosis_eval.runner"],
    "evaluators": ["ignosis_eval.contracts.gold_label", "ignosis_eval.integrity.freeze", "ignosis_eval.scoring",
                   "ignosis_eval.metrics", "ignosis_eval.runner", "ignosis_eval.benchmark"],
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


@pytest.mark.parametrize("subpkg", sorted(FORBIDDEN))
def test_import_boundaries(subpkg):
    root = PKG / subpkg
    if not root.exists():
        pytest.skip(f"{subpkg} not present yet")
    offenders = []
    for py in root.rglob("*.py"):
        for imp in _imports(py):
            for bad in FORBIDDEN[subpkg]:
                if imp == bad or imp.startswith(bad + "."):
                    offenders.append(f"{py.relative_to(PKG)} imports {imp}")
    assert not offenders, "\n".join(offenders)
