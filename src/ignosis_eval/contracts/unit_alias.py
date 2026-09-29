"""Opaque unit aliases and the pre-run payload test — experiment-protocol.md P-17 (SC-05).

  1. Every evaluation unit gets a random opaque alias per run (`u_` + 8 hex, e.g. `u_7f3a91c2`). The alias -> item
     mapping is stored only in BENCH_PRIVATE_DIR and in the run's private area (runner/aliases.py).
  2. No bench-a1 item ID, pair ID, pack name, split name or source filename may appear in any evaluator input.
  3. Rendered audio is renamed to its alias before any ASR or evaluator step (pipeline/normalize.py).
  4. A pre-run test fails the run if any evaluator-bound payload matches the item-ID pattern
     `^(G|M|K|C|R|P|J|X|A|AB|E|S|MI|MC|MD|SN|RT|CAL)-` or contains a pack/split word.

Scope of rule 4 (convention, docs/spec-reconciliation.md §3/§4): the test runs on the *item-dependent* payload —
every string value of the NormalizedInput (turn text, header, metadata fields; schema keys are fixed and excluded)
and the file path handed to ASR (renamed to the alias). The rubric/prompt template text is identical for every unit
and itself contains the pack word "language" (frozen rubric text), so it cannot be in scope without failing every
run. The item-ID pattern is applied at every token start (a position not
preceded by a letter or digit), case-sensitive as written; pack/split words are matched as whole words, any case.
Both are deliberately broad (fail closed): ordinary words such as "E-mail", "X-ray", "language" or the name "Dev"
in a transcript fail the run.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable

from ignosis_eval.contracts.enums import Pack, Split

UNIT_ALIAS_PATTERN = r"^u_[0-9a-f]{8}$"
ITEM_ID_PATTERN = re.compile(r"^(G|M|K|C|R|P|J|X|A|AB|E|S|MI|MC|MD|SN|RT|CAL)-")  # P-17 rule 4, verbatim
PAIR_ID_PATTERN = re.compile(r"^(MP|CP|AP|TW)-\d")  # §12 pair / twin ids (P-17 rule 2)
_TOKEN_START = re.compile(r"(?<![A-Za-z0-9])(?=[A-Z])")

# Pack and split names: this implementation's enums plus the §12 spellings ("Red team", "Language pack").
PACK_SPLIT_WORDS: tuple[str, ...] = tuple(sorted(
    {p.value for p in Pack} | {s.value for s in Split} | {"red team", "red-team", "red_team", "language"}))
_WORDS = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(w) for w in
                                                         sorted(PACK_SPLIT_WORDS, key=len, reverse=True))
                    + r")(?![A-Za-z0-9])", re.IGNORECASE)


def new_unit_alias() -> str:
    """A fresh random alias; it carries no benchmark metadata and differs between runs."""
    return "u_" + secrets.token_hex(4)


def payload_violations(text: str) -> list[str]:
    """P-17 rule 4 on one payload string: item-ID-pattern hits (at any token start), pair ids and pack/split words."""
    out: list[str] = []
    for m in _TOKEN_START.finditer(text):
        tail = text[m.start(): m.start() + 12]
        hit = ITEM_ID_PATTERN.match(tail) or PAIR_ID_PATTERN.match(tail)
        if hit:
            out.append(f"identifier-pattern {text[m.start(): m.start() + len(hit.group(0)) + 4]!r}")
    out += [f"pack/split word {m.group(0)!r}" for m in _WORDS.finditer(text)]
    return out


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


__all__ = ["ITEM_ID_PATTERN", "PACK_SPLIT_WORDS", "PAIR_ID_PATTERN", "UNIT_ALIAS_PATTERN", "new_unit_alias",
           "payload_violations", "strings_of"]
