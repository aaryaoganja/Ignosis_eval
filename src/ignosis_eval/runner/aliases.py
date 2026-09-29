"""Opaque unit aliases for a run — experiment-protocol.md P-17 (SC-05).

  * `assign_unit_aliases`: one fresh random alias per unit per run (collision-free within the run).
  * `write_unit_alias_mapping`: the alias -> item mapping is written write-once to the run's private area
    (<results>/blinding/<run_id>/unit_alias_mapping.json, guard-protected and outside the scorer's input) and, when
    BENCH_PRIVATE_DIR is configured, to <BENCH_PRIVATE_DIR>/run_aliases/<run_id>.json. Its hash goes into the run
    manifest (frozen-contract §17: "alias mapping hash").
  * `prerun_payload_check`: P-17 rule 4 over every evaluator-bound payload (contracts/unit_alias.py for the scope);
    any hit fails the run before a system is called.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.unit_alias import (
    ITEM_ID_PREFIX_PATTERN,
    evaluator_payload,
    new_unit_alias,
    payload_violations,
)
from ignosis_eval.integrity.freeze import IntegrityError
from ignosis_eval.integrity.hashing import file_canonical_sha256
from ignosis_eval.runner.storage import ResultsLayout, write_json_once

UNIT_ALIAS_MAPPING_FILE = "unit_alias_mapping.json"
PRIVATE_ALIAS_DIR = "run_aliases"


class OpaqueAliasViolation(IntegrityError):
    """P-17: an evaluator-bound payload carries an item id, pair id, pack/split word or file name (fail closed)."""


def assign_unit_aliases(unit_ids: Iterable[str], new_alias=new_unit_alias) -> dict[str, str]:
    out: dict[str, str] = {}
    taken: set[str] = set()
    for uid in unit_ids:
        alias = new_alias()
        while alias in taken:
            alias = new_alias()
        taken.add(alias)
        out[uid] = alias
    return out


def write_unit_alias_mapping(results: ResultsLayout, run_id: str, aliases: Mapping[str, str],
                             units: Mapping[str, tuple[str, str]], private_root: Path | None) -> str:
    """aliases: unit_id -> alias; units: unit_id -> (item_id, unit_mode). Returns the mapping file's hash."""
    body = {"run_id": run_id, "rule": "experiment-protocol P-17",
            "mapping": {aliases[uid]: {"unit_id": uid, "item_id": units[uid][0], "unit_mode": units[uid][1]}
                        for uid in sorted(aliases)}}
    d = results.blinding / run_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / UNIT_ALIAS_MAPPING_FILE
    write_json_once(path, body)
    if private_root is not None:
        pdir = Path(private_root) / PRIVATE_ALIAS_DIR
        pdir.mkdir(parents=True, exist_ok=True)
        write_json_once(pdir / f"{run_id}.json", body)
    return file_canonical_sha256(path)


def load_unit_alias_mapping(results: ResultsLayout, run_id: str, expected_sha256: str) -> dict[str, dict]:
    path = results.blinding / run_id / UNIT_ALIAS_MAPPING_FILE
    if not path.exists() or file_canonical_sha256(path) != expected_sha256:
        raise IntegrityError("unit alias mapping missing or its hash differs from the run manifest (fail closed)")
    return json.loads(path.read_text(encoding="utf-8"))["mapping"]


def prerun_payload_check(nis: Mapping[str, NormalizedInput], item_tokens: Mapping[str, Iterable[str]]) -> int:
    """P-17 rule 4 on every unit's evaluator-bound payload (identity only; contracts/unit_alias.py for the scope).
    `item_tokens` (unit_id -> item id, directory, source file names) adds the unit's own identifiers. Returns the
    number of payload strings checked; any hit fails the run before a system is called."""
    checked = 0
    problems: list[str] = []
    for uid, ni in nis.items():
        own = [t for t in item_tokens.get(uid, ()) if t]
        free, meta = evaluator_payload(ni.to_json_dict())
        for s, is_free in [(x, True) for x in free] + [(x, False) for x in meta]:
            checked += 1
            hits = payload_violations(s, free_text=is_free, own_tokens=own)
            if hits:
                problems.append(f"unit {ni.unit_alias}: " + "; ".join(sorted(set(hits))))
    if problems:
        raise OpaqueAliasViolation("P-17 pre-run payload test failed (fail closed): " + " | ".join(problems[:20]))
    return checked


__all__ = ["ITEM_ID_PREFIX_PATTERN", "OpaqueAliasViolation", "PRIVATE_ALIAS_DIR", "UNIT_ALIAS_MAPPING_FILE",
           "assign_unit_aliases", "load_unit_alias_mapping", "prerun_payload_check", "write_unit_alias_mapping"]
