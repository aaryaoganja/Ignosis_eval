from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path

import pytest
import yaml

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "benchmark_smoke"


def _make_writable(root: Path) -> None:
    for p in [root, *root.rglob("*")]:
        p.chmod(p.stat().st_mode | stat.S_IWUSR | (stat.S_IXUSR if p.is_dir() else 0))


@pytest.fixture
def smoke_root(tmp_path) -> Path:
    """A private, writable copy of the smoke-test fixture benchmark (TEST FIXTURE, not benchmark gold)."""
    dst = tmp_path / "bench"
    shutil.copytree(FIXTURE_ROOT, dst)
    _make_writable(dst)
    return dst


def edit_yaml(path: Path, fn) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def edit_json(path: Path, fn) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
