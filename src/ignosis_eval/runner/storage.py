"""Append-only run storage.

* A run directory is created with exist_ok=False: an existing run can never be reopened or overwritten.
* Every file is written with O_EXCL ('x' mode) and then made read-only; a second write raises.
* runs/LEDGER.jsonl is an append-only journal of every run start/finish.
Layout: see ignosis_eval/contracts/run_manifest.py.
"""

from __future__ import annotations

import json
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ignosis_eval.canonical import canonical_json_pretty
from ignosis_eval.contracts.run_manifest import rep_dir


class AppendOnlyError(RuntimeError):
    """Attempt to overwrite or reopen something that is write-once."""


def new_run_id(evaluator: str, split: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now:%Y%m%dT%H%M%SZ}-{evaluator}-{split}-{secrets.token_hex(4)}"


def _write_once(path: Path, text: str) -> None:
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(text)
    except FileExistsError as exc:
        raise AppendOnlyError(f"refusing to overwrite {path}") from exc
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def write_json_once(path: Path, obj: Any) -> None:
    _write_once(path, canonical_json_pretty(obj))


def write_jsonl_once(path: Path, rows: list[dict[str, Any]]) -> None:
    _write_once(path, "".join(json.dumps(r, sort_keys=True, ensure_ascii=False, default=str) + "\n" for r in rows))


class RunStore:
    def __init__(self, runs_root: str | Path):
        self.root = Path(runs_root)

    def create_run(self, run_id: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        d = self.root / run_id
        try:
            d.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise AppendOnlyError(f"run directory already exists: {d}") from exc
        return d

    def create_rep_dir(self, run_dir: Path, item_id: str, rep: int) -> Path:
        d = rep_dir(run_dir, item_id, rep)
        try:
            d.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise AppendOnlyError(f"repetition directory already exists: {d}") from exc
        return d

    def append_ledger(self, entry: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / "LEDGER.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")

    @staticmethod
    def seal(run_dir: Path) -> None:
        """Remove write permission from every directory of a finished run (files are already read-only)."""
        for p in sorted(run_dir.rglob("*"), reverse=True):
            if p.is_dir():
                p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        run_dir.chmod(run_dir.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
