"""Gold/evaluator separation: the evaluator process cannot read, write or list protected paths."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from ignosis_eval.integrity.guard import ProtectedPathGuard, ProtectedPathViolation, guard_active


@pytest.fixture
def tree(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    (gold / "g.json").write_text('{"x": 1}')
    data = tmp_path / "dataset"
    data.mkdir()
    (data / "input.json").write_text("{}")
    link = tmp_path / "innocent.json"
    link.symlink_to(gold / "g.json")
    return tmp_path, gold, data, link


def test_reads_writes_listing_blocked(tree):
    root, gold, data, link = tree
    with ProtectedPathGuard([gold]):
        assert guard_active()
        with pytest.raises(ProtectedPathViolation):
            (gold / "g.json").read_text()
        with pytest.raises(ProtectedPathViolation):
            open(gold / "g.json", "w")
        with pytest.raises(ProtectedPathViolation):
            os.open(str(gold / "new.json"), os.O_CREAT | os.O_WRONLY)
        with pytest.raises(ProtectedPathViolation):
            os.listdir(gold)
        with pytest.raises(ProtectedPathViolation):
            os.remove(gold / "g.json")
        with pytest.raises(ProtectedPathViolation):
            os.rename(gold / "g.json", root / "stolen.json")
        with pytest.raises(ProtectedPathViolation):
            shutil.copyfile(gold / "g.json", root / "copy.json")
        with pytest.raises(ProtectedPathViolation):
            os.chmod(gold / "g.json", 0o666)
        with pytest.raises(ProtectedPathViolation):
            link.read_text()  # symlink resolving into gold
        with pytest.raises(ProtectedPathViolation):
            open(os.path.relpath(gold / "g.json"))  # relative path
        assert (data / "input.json").read_text() == "{}"  # unprotected paths still work
    assert not guard_active()
    assert (gold / "g.json").read_text() == '{"x": 1}'


def test_subprocess_blocked(tree):
    _, gold, _, _ = tree
    with ProtectedPathGuard([gold]):
        with pytest.raises(ProtectedPathViolation):
            subprocess.run(["cat", str(gold / "g.json")], check=False)
        with pytest.raises(ProtectedPathViolation):
            os.system("true")
    subprocess.run(["true"], check=True)  # allowed again afterwards


def test_guard_restores_after_exception(tree):
    _, gold, _, _ = tree
    with pytest.raises(RuntimeError):
        with ProtectedPathGuard([gold]):
            raise RuntimeError("boom")
    assert not guard_active()
    assert (gold / "g.json").exists()
