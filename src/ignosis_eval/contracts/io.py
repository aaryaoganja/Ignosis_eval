"""Small, dependency-free helpers for reading/writing contract artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from ignosis_eval.canonical import canonical_json_pretty

M = TypeVar("M", bound=BaseModel)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_yaml(path: str | Path) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_model(path: str | Path, cls: type[M]) -> M:
    p = Path(path)
    data = read_yaml(p) if p.suffix in (".yaml", ".yml") else read_json(p)
    return cls.model_validate(data)


def model_to_pretty_json(model: BaseModel) -> str:
    return canonical_json_pretty(model.model_dump(mode="json"))
