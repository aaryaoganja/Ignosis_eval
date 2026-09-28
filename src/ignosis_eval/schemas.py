"""Export JSON Schemas for every contract (schemas/*.schema.json). Kept in sync by a test."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ignosis_eval.contracts.benchmark import BenchManifest, GoldManifest, ItemMeta
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.case_card import CaseCard
from ignosis_eval.contracts.evaluation_record import EvaluationRecord
from ignosis_eval.contracts.extraction import ExtractionOutput
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.contracts.profile import ProfileSpec
from ignosis_eval.contracts.registries import Registries
from ignosis_eval.contracts.run_manifest import (
    BlindViewManifest,
    RunCompletion,
    RunManifest,
    ScoringManifest,
)

CONTRACTS: dict[str, type[BaseModel]] = {
    "normalized_input": NormalizedInput,
    "evaluation_record": EvaluationRecord,
    "extraction": ExtractionOutput,
    "gold_label": GoldLabel,
    "case_card": CaseCard,
    "item_meta": ItemMeta,
    "registries": Registries,
    "profile": ProfileSpec,
    "bench_manifest": BenchManifest,
    "gold_manifest": GoldManifest,
    "run_manifest": RunManifest,
    "run_completion": RunCompletion,
    "blind_view_manifest": BlindViewManifest,
    "scoring_manifest": ScoringManifest,
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
