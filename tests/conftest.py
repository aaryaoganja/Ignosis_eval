from __future__ import annotations

import stat
from pathlib import Path

import pytest

from ignosis_eval.benchmark.layout import BenchLayout
import factories as F


def make_writable(root: Path) -> None:
    for p in [root, *root.rglob("*")]:
        p.chmod(p.stat().st_mode | stat.S_IWUSR | (stat.S_IXUSR if p.is_dir() else 0))


@pytest.fixture
def spec():
    return F.spec()


@pytest.fixture
def layout(tmp_path, monkeypatch) -> BenchLayout:
    """An empty stub bench (dev in tmp/bench, private in tmp/private). TEST FIXTURE, not benchmark content."""
    monkeypatch.delenv("BENCH_PRIVATE_DIR", raising=False)
    lay = BenchLayout(tmp_path / "bench", tmp_path / "private")
    F.write_registries(lay)
    yield lay
    for d in (tmp_path / "bench", tmp_path / "private", tmp_path / "results"):
        if d.exists():
            make_writable(d)
