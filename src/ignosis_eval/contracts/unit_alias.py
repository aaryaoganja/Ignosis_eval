"""Opaque unit aliases and the pre-run payload test — experiment-protocol.md P-17 (SC-05).

  1. Every evaluation unit gets a random opaque alias per run (`u_` + 8 hex, e.g. `u_7f3a91c2`). The alias -> item
     mapping is stored only in BENCH_PRIVATE_DIR and in the run's private area (runner/aliases.py).
  2. No bench-a1 item ID, pair ID, pack name, split name or source filename may appear in any evaluator input.
  3. Rendered audio is renamed to its alias before any ASR or evaluator step (pipeline/normalize.py).
  4. A pre-run test fails the run if an evaluator-bound payload carries benchmark identity.

Scope of rule 4 (implementation safety rule, docs/spec-reconciliation.md §3.35, docs/bd-changelog.md): the test
targets benchmark *identity*, never ordinary vocabulary. It flags
  * bench-a1 item ids in their canonical shape, built from the P-17 prefixes
    `^(G|M|K|C|R|P|J|X|A|AB|E|S|MI|MC|MD|SN|RT|CAL)-` (e.g. C-08, MI-G1-01, MD-G5, SN-D01, G-02-N5, C-04-EN), any case;
  * pair and twin ids (MP-01, CP-01, AP-01, TW-01);
  * rubric check ids (G1..G9 with sub-rules, UND/ACC/RES/COM/TRT/POL/PLT/EXE-nn) in free text (transcript turns and
    header values; the front-end metadata legitimately names G7 / POL-01);
  * benchmark-specific labels and aliases (bench-a1, holdout, redteam, language_twin, tuning_only, SYS-n blind aliases,
    BD-nn benchmark decision ids);
  * the unit's own source identifiers (item id, item directory, artifact file names), passed in by the runner.
Ordinary words ("language", "email", "E-mail", "X-ray", "dev", "core", "micro", "calibration", …) are not leaks.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable

UNIT_ALIAS_PATTERN = r"^u_[0-9a-f]{8}$"
ITEM_ID_PREFIX_PATTERN = "^(G|M|K|C|R|P|J|X|A|AB|E|S|MI|MC|MD|SN|RT|CAL)-"  # P-17 rule 4, verbatim
_B = r"(?<![A-Za-z0-9])"
_E = r"(?![A-Za-z0-9])"
# canonical bench-a1 id shapes for every P-17 prefix (frozen-contract §12)
ITEM_ID_TOKEN = re.compile(
    _B + r"(?:(?:G|M|K|C|R|P|J|X|A|E|S)-\d{2}|AB-\d{2}|MC-\d{2}|MI-G\d-\d{2}|MD-[GC]\d|SN-[DH]\d{2}|RT-\d{2}"
    r"|CAL-\d{2})(?:-[A-Z0-9]+)*(?:@audio)?" + _E, re.IGNORECASE)
PAIR_ID_TOKEN = re.compile(_B + r"(?:MP|CP|AP|TW)-\d{2}" + _E, re.IGNORECASE)
CHECK_ID_TOKEN = re.compile(_B + r"(?:G[1-9][a-c]?|(?:UND|ACC|RES|COM|TRT|POL|PLT|EXE)-\d{2}[a-z]?)" + _E)
BENCHMARK_LABELS: tuple[str, ...] = ("bench-a1", "bench_a1", "holdout", "redteam", "red_team", "language_twin",
                                     "tuning_only")
LABEL_TOKEN = re.compile(_B + r"(?:" + "|".join(re.escape(w) for w in BENCHMARK_LABELS) + r"|SYS-\d+|BD-\d{2})" + _E,
                         re.IGNORECASE)


def new_unit_alias() -> str:
    """A fresh random alias; it carries no benchmark metadata and differs between runs."""
    return "u_" + secrets.token_hex(4)


def payload_violations(text: str, *, free_text: bool = True, own_tokens: Iterable[str] = ()) -> list[str]:
    """P-17 rule 4 on one payload string. `free_text` adds rubric check ids (transcript turns, header values);
    `own_tokens` are the unit's own source identifiers (item id, directory, file names), matched in any case."""
    out = [f"item id {m.group(0)!r}" for m in ITEM_ID_TOKEN.finditer(text)]
    out += [f"pair/twin id {m.group(0)!r}" for m in PAIR_ID_TOKEN.finditer(text)]
    out += [f"benchmark label {m.group(0)!r}" for m in LABEL_TOKEN.finditer(text)]
    if free_text:
        out += [f"check id {m.group(0)!r}" for m in CHECK_ID_TOKEN.finditer(text)]
    out += [f"source identifier {t!r}" for t in own_tokens if t and own_token(t).search(text)]
    return out


def own_token(token: str) -> re.Pattern[str]:
    """A unit's own identifier as a whole token (any case): `C-01` matches "c-01" and "C-01.txt" but not the
    front end's step name "DC-01-transcript-markers" (the same boundaries as the item-id pattern)."""
    return re.compile(_B + re.escape(token) + _E, re.IGNORECASE)


def evaluator_payload(ni_json: dict) -> tuple[list[str], list[str]]:
    """(free-text strings: turn texts and header values, metadata strings: every other string value)."""
    free: list[str] = []
    turns = []
    for t in ni_json.get("turns", []):
        free += [t[k] for k in ("text", "supplied_text", "asr_text") if isinstance(t.get(k), str)]
        turns.append({k: v for k, v in t.items() if k not in ("text", "supplied_text", "asr_text")})
    free += [v for v in (ni_json.get("header") or {}).values() if isinstance(v, str)]
    rest = {k: v for k, v in ni_json.items() if k not in ("turns", "header")}
    return free, list(strings_of(rest)) + list(strings_of(turns))


def strings_of(obj: object) -> Iterable[str]:
    """Every string value of a JSON-like object (the payload the evaluator receives). Keys are the fixed contract
    schema (e.g. `Turn.language`), identical for every unit, so they carry no item metadata and are not checked."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from strings_of(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from strings_of(v)
    elif isinstance(obj, str):
        yield obj


__all__ = ["BENCHMARK_LABELS", "CHECK_ID_TOKEN", "ITEM_ID_PREFIX_PATTERN", "ITEM_ID_TOKEN", "LABEL_TOKEN",
           "PAIR_ID_TOKEN", "UNIT_ALIAS_PATTERN", "evaluator_payload", "new_unit_alias", "own_token",
           "payload_violations", "strings_of"]
