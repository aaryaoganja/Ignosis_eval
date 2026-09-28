"""Append-only holdout registry maintenance."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ignosis_eval.benchmark.checks import BenchmarkIntegrityError, check_benchmark
from ignosis_eval.benchmark.layout import BenchmarkLayout
from ignosis_eval.contracts.benchmark import HoldoutRegistry, HoldoutRegistryEntry
from ignosis_eval.contracts.enums import Split
from ignosis_eval.contracts.io import model_to_pretty_json, read_json


def register_holdout(root: str | Path) -> list[str]:
    """Add every current holdout case not yet registered. Never removes or rewrites entries.

    Refuses to run if the benchmark has errors (so a mis-filed case cannot be registered).
    Returns the newly registered case ids.
    """
    layout = BenchmarkLayout(root)
    rep = check_benchmark(root)
    rep.raise_if_errors()
    registry = (HoldoutRegistry.model_validate(read_json(layout.holdout_registry_path))
                if layout.holdout_registry_path.exists() else HoldoutRegistry())
    now = datetime.now(timezone.utc)
    added: list[str] = []
    for cid, lc in sorted(rep.cases.items()):
        if lc.split is Split.HOLDOUT and cid not in registry.ids():
            if lc.fingerprint is None:
                raise BenchmarkIntegrityError(f"cannot register {cid}: input not loadable")
            registry.entries.append(HoldoutRegistryEntry(case_id=cid, content_fingerprint=lc.fingerprint,
                                                         registered_at=now))
            added.append(cid)
    if added:
        layout.manifests_dir.mkdir(parents=True, exist_ok=True)
        layout.holdout_registry_path.write_text(model_to_pretty_json(registry), encoding="utf-8")
    return added
