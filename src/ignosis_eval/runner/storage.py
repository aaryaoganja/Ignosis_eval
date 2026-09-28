"""Append-only results storage (experiment-protocol P-11).

    <results>/runs/<run_id>/manifest.json, completion.json
    <results>/runs/<run_id>/<system>/<item_id>__<mode>/rep_<k>/{normalized_input.json, llm_requests.jsonl,
        llm_responses.jsonl, evaluation_record.json, timing.json, usage.json, errors.json}
    <results>/runs/<run_id>/A+/<item_id>__<mode>/rep_<k>/derivation_log.json
    <results>/runs/LEDGER.jsonl, locked_runs.jsonl        append-only journals
    <results>/blinding/<run_id>/alias_mapping.json         P-10 (outside the scorer's input path)
    <results>/blinded/<run_id>/...                         P-10 aliased scoring view
    <results>/scoring/<run_id>[__<suffix>]/...             scorer outputs

* A run directory is created with exist_ok=False: an existing run can never be reopened or overwritten.
* Every file is written with O_EXCL ('x' mode) and then made read-only; a second write raises.
* Tuning runs use run ids `dev-<round>-<timestamp>` (P-11); other runs `<kind>-<timestamp>-<random>`.
"""

from __future__ import annotations

import json
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ignosis_eval.canonical import canonical_json_pretty
from ignosis_eval.contracts.run_manifest import rep_dir


class AppendOnlyError(RuntimeError):
    """Attempt to overwrite or reopen something that is write-once."""


def new_run_id(kind: str, tuning_round: int | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    stamp = f"{now:%Y%m%dT%H%M%SZ}"
    if kind == "dev_tuning":
        if tuning_round is None:
            raise ValueError("tuning runs need a round number")
        return f"dev-{tuning_round}-{stamp}"
    return f"{kind}-{stamp}-{secrets.token_hex(4)}"


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


@dataclass(frozen=True)
class ResultsLayout:
    root: Path

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def blinding(self) -> Path:
        return self.root / "blinding"

    @property
    def blinded(self) -> Path:
        return self.root / "blinded"

    @property
    def scoring(self) -> Path:
        return self.root / "scoring"

    @property
    def locked_runs(self) -> Path:
        return self.runs / "locked_runs.jsonl"

    def run_dir(self, run_id: str) -> Path:
        return self.runs / run_id


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

    def create_rep_dir(self, run_dir: Path, system: str, item_id: str, unit_mode: str, rep: int) -> Path:
        d = rep_dir(run_dir, system, item_id, unit_mode, rep)
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
