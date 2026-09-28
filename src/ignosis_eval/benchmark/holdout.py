"""Holdout discipline — append-only locked-run registry (frozen-contract §13 rules 2–3, experiment-protocol
P-8 / P-9).

  * one locked holdout run per frozen evaluator version (evaluator tag); a re-run needs a new evaluator
    version, and any post-lock change demotes the holdout to dev (P-8 step 6);
  * the red-team run comes after the holdout run of the same frozen evaluators (P-9 step 3);
  * the registry is a JSON-lines file that is only ever appended to; entries are never rewritten.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime

from ignosis_eval.contracts._base import Contract, NonEmptyStr, Sha256Hex

LOCKED_RUNS_FILE = "locked_runs.jsonl"


class LockedRunError(RuntimeError):
    pass


class LockedRunEntry(Contract):
    run_id: NonEmptyStr
    kind: Literal["locked_holdout", "locked_redteam"]
    evaluator_tag: NonEmptyStr
    commit: NonEmptyStr
    run_manifest_sha256: Sha256Hex
    registered_at: AwareDatetime


def load_locked_runs(path: Path) -> list[LockedRunEntry]:
    if not path.exists():
        return []
    return [LockedRunEntry.model_validate(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def check_locked_run_allowed(path: Path, kind: str, evaluator_tag: str) -> None:
    runs = load_locked_runs(path)
    if any(r.kind == kind and r.evaluator_tag == evaluator_tag for r in runs):
        raise LockedRunError(f"a {kind} run already exists for evaluator {evaluator_tag}: one locked run per frozen "
                             "evaluator version (§13 rule 3); a change needs a new evaluator version")
    if kind == "locked_redteam" and not any(r.kind == "locked_holdout" and r.evaluator_tag == evaluator_tag
                                            for r in runs):
        raise LockedRunError("the red-team run follows the holdout run of the same frozen evaluators (P-9)")


def register_locked_run(path: Path, entry: LockedRunEntry) -> None:
    check_locked_run_allowed(path, entry.kind, entry.evaluator_tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:  # append-only
        fh.write(json.dumps(entry.model_dump(mode="json"), sort_keys=True) + "\n")
