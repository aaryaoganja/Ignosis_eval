"""Export JSON Schemas for every contract (schemas/*.schema.json). Kept in sync by a test."""

from __future__ import annotations

import json
from pathlib import Path

from ignosis_eval.contracts.benchmark import BenchmarkManifest, GoldManifest, HoldoutRegistry
from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.case_card import CaseCard
from ignosis_eval.contracts.evaluation_record import EvaluationRecord
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.contracts.profile import Profile
from ignosis_eval.contracts.run_manifest import RunCompletion, RunManifest

CONTRACTS = {
    "canonical_input": CanonicalInput,
    "evaluation_record": EvaluationRecord,
    "gold_label": GoldLabel,
    "case_card": CaseCard,
    "profile": Profile,
    "benchmark_manifest": BenchmarkManifest,
    "gold_manifest": GoldManifest,
    "holdout_registry": HoldoutRegistry,
    "run_manifest": RunManifest,
    "run_completion": RunCompletion,
}


def render_schemas() -> dict[str, str]:
    return {name: json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
            for name, model in CONTRACTS.items()}


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in render_schemas().items():
        p = out_dir / f"{name}.schema.json"
        p.write_text(text, encoding="utf-8")
        paths.append(p)
    return paths
