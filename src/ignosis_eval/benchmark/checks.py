"""Benchmark integrity checks (structure, splits, pairs, gold references, holdout discipline).

`check_benchmark` never mutates anything. It returns a report; callers that must fail closed call
`report.raise_if_errors()`. Manifest/hash drift checks are added by integrity/freeze.py.

Check ids (see docs/benchmark-authoring.md):
  B001 layout problem (unknown split dir, stray files)       B020 case card invalid / rule error
  B002 invalid case id                                       B021 card case_id / split disagree with location
  B003 duplicate case id across splits                       B022 card missing for a dataset case
  B004 missing input.json                                    B023 card without dataset case (warning)
  B005 invalid Canonical Input                               B030 orphan gold file
  B006 duplicate call_id                                     B031 gold split/location disagrees with case
  B007 input audio file missing / hash mismatch              B032 gold item_id disagrees with file name
  B008 identical content in two splits (leakage)             B033 gold invalid
  B009 identical content within a split (warning)            B034 gold missing (error if required)
  B010 case/card id leaks scenario (warning)                 B035 gold scenario != card scenario (warning)
  B040 holdout registry violation                            B036 gold uses ids unknown to the profile
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from ignosis_eval.benchmark.case_card_rules import (
    check_card_against_input,
    check_card_against_profile,
    check_card_set,
    leaked_tokens,
    load_case_card_file,
)
from ignosis_eval.benchmark.layout import BenchmarkLayout, content_fingerprint
from ignosis_eval.canonical import sha256_bytes
from ignosis_eval.contracts._base import CASE_ID_PATTERN
from ignosis_eval.contracts.benchmark import HoldoutRegistry
from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.case_card import CaseCard
from ignosis_eval.contracts.enums import Split
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.contracts.io import read_json
from ignosis_eval.contracts.profile import Profile


class BenchmarkIntegrityError(RuntimeError):
    """Raised when a benchmark check fails and the caller must fail closed."""


@dataclass(frozen=True)
class Issue:
    check_id: str
    severity: Literal["error", "warning"]
    message: str
    case_id: str | None = None

    def __str__(self) -> str:
        who = f"[{self.case_id}] " if self.case_id else ""
        return f"{self.severity.upper()} {self.check_id} {who}{self.message}"


@dataclass
class CheckReport:
    issues: list[Issue] = field(default_factory=list)
    cases: dict[str, "LoadedCase"] = field(default_factory=dict)

    def add(self, check_id: str, severity: Literal["error", "warning"], message: str, case_id: str | None = None):
        self.issues.append(Issue(check_id, severity, message, case_id))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_errors(self) -> None:
        if self.errors:
            lines = "\n".join(str(i) for i in self.errors)
            raise BenchmarkIntegrityError(f"benchmark integrity check failed ({len(self.errors)} errors):\n{lines}")


@dataclass
class LoadedCase:
    split: Split
    case_id: str
    input: CanonicalInput | None = None
    card: CaseCard | None = None
    gold: GoldLabel | None = None
    fingerprint: str | None = None
    extra_files: list[Path] = field(default_factory=list)


def _load_input(path: Path) -> CanonicalInput:
    return CanonicalInput.model_validate(read_json(path))


def check_benchmark(
    root: str | Path, *, require_gold: bool = False, profile: Profile | None = None
) -> CheckReport:
    layout = BenchmarkLayout(root)
    rep = CheckReport()

    cases, problems = layout.discover_dataset()
    for p in problems:
        rep.add("B001", "error", p)

    # ------------------------------------------------------------------------------ dataset cases
    seen_ids: dict[str, Split] = {}
    call_ids: dict[str, str] = {}
    for cp in cases:
        cid = cp.case_id
        if not re.match(CASE_ID_PATTERN, cid):
            rep.add("B002", "error", f"invalid case id {cid!r} (pattern {CASE_ID_PATTERN})", cid)
            continue
        if cid in seen_ids:
            rep.add("B003", "error", f"case id appears in splits {seen_ids[cid]} and {cp.split}", cid)
            continue
        seen_ids[cid] = cp.split
        lc = LoadedCase(cp.split, cid)
        rep.cases[cid] = lc
        if not cp.input_path.exists():
            rep.add("B004", "error", "missing input.json", cid)
        else:
            try:
                lc.input = _load_input(cp.input_path)
            except (ValidationError, json.JSONDecodeError) as exc:
                rep.add("B005", "error", f"invalid canonical input: {exc}".replace("\n", " "), cid)
        lc.extra_files = sorted(p for p in cp.case_dir.iterdir() if p.is_file() and p.name != "input.json")
        if lc.input is not None:
            inp = lc.input
            if inp.call_id in call_ids:
                rep.add("B006", "error", f"call_id {inp.call_id} also used by {call_ids[inp.call_id]}", cid)
            call_ids[inp.call_id] = cid
            if inp.audio is not None:
                apath = cp.case_dir / inp.audio.uri
                if not apath.exists():
                    rep.add("B007", "error", f"audio file {inp.audio.uri} missing", cid)
                elif sha256_bytes(apath.read_bytes()) != inp.audio.sha256:
                    rep.add("B007", "error", f"audio file {inp.audio.uri} sha256 mismatch", cid)
            lc.fingerprint = content_fingerprint(inp)

    by_fp: dict[str, list[LoadedCase]] = defaultdict(list)
    for lc in rep.cases.values():
        if lc.fingerprint:
            by_fp[lc.fingerprint].append(lc)
    for members in by_fp.values():
        if len(members) > 1:
            splits = {m.split for m in members}
            ids = sorted(m.case_id for m in members)
            if len(splits) > 1:
                rep.add("B008", "error", f"identical input content across splits {sorted(splits)}: {ids}")
            else:
                rep.add("B009", "warning", f"identical input content within split: {ids}")

    # ------------------------------------------------------------------------------ case cards
    card_files, card_problems = layout.discover_cards()
    for p in card_problems:
        rep.add("B001", "error", p)
    cards: list[CaseCard] = []
    cards_by_id: dict[str, tuple[str, Path]] = {}
    for split_name, cid, path in card_files:
        card, issues = load_case_card_file(path)
        for i in issues:
            rep.add("B020", i.severity, f"{i.rule_id}: {i.message}", cid)
        if cid in cards_by_id:
            rep.add("B003", "error", f"case card for {cid} exists in splits {cards_by_id[cid][0]} and {split_name}", cid)
        cards_by_id[cid] = (split_name, path)
        if card is None:
            continue
        cards.append(card)
        if card.case_id != cid:
            rep.add("B021", "error", f"card file name {cid} but case_id {card.case_id}", cid)
        if card.split.value != split_name:
            rep.add("B021", "error", f"card filed under {split_name} but declares split={card.split}", cid)
        lc = rep.cases.get(cid)
        if lc is None:
            rep.add("B023", "warning", "case card has no dataset case (not yet produced?)", cid)
            continue
        if lc.split.value != split_name:
            rep.add("B021", "error", f"dataset case in {lc.split} but card filed under {split_name}", cid)
        lc.card = card
        if lc.input is not None:
            for i in check_card_against_input(card, lc.input):
                rep.add("B020", i.severity, f"{i.rule_id}: {i.message}", cid)
        if profile is not None:
            for i in check_card_against_profile(card, profile):
                rep.add("B020", i.severity, f"{i.rule_id}: {i.message}", cid)
    for i in check_card_set(cards):
        rep.add("B020", i.severity, f"{i.rule_id}: {i.message}", i.case_id)
    for cid, lc in rep.cases.items():
        if cid not in cards_by_id:
            rep.add("B022", "error", "dataset case has no case card", cid)

    # ------------------------------------------------------------------------------ gold
    gold_files, gold_problems = layout.discover_gold()
    for p in gold_problems:
        rep.add("B001", "error", p)
    gold_seen: set[str] = set()
    for split_name, cid, path in gold_files:
        if cid in gold_seen:
            rep.add("B003", "error", "gold file exists in more than one split", cid)
        gold_seen.add(cid)
        lc = rep.cases.get(cid)
        if lc is None:
            rep.add("B030", "error", f"orphan gold file gold/{split_name}/{path.name} (no such case)", cid)
            continue
        if lc.split.value != split_name:
            rep.add("B031", "error", f"case is in {lc.split} but its gold is filed under {split_name}", cid)
        try:
            gold = GoldLabel.model_validate(read_json(path))
        except (ValidationError, json.JSONDecodeError) as exc:
            rep.add("B033", "error", f"invalid gold: {exc}".replace("\n", " "), cid)
            continue
        lc.gold = gold
        if gold.item_id != cid:
            rep.add("B032", "error", f"gold item_id {gold.item_id} != file name {cid}", cid)
        if gold.split is not lc.split:
            rep.add("B031", "error", f"gold declares split={gold.split} but case is in {lc.split}", cid)
        if lc.card is not None and gold.scenario != lc.card.scenario:
            rep.add("B035", "warning", f"gold scenario {gold.scenario!r} != card scenario {lc.card.scenario!r}", cid)
        if profile is not None:
            unknown = [g.gate_id for g in gold.expected_gates if profile.gate_def(g.gate_id) is None]
            unknown += [d.defect_id for d in gold.expected_defects if profile.defect_def(d.defect_id) is None]
            unknown += [d for d in gold.acceptable_extra_defects if profile.defect_def(d) is None]
            if unknown:
                rep.add("B036", "error", f"gold references ids unknown to profile: {unknown}", cid)
    for cid, lc in rep.cases.items():
        if lc.gold is None and cid not in gold_seen:
            rep.add("B034", "error" if require_gold else "warning", "no gold label for case", cid)

    # ------------------------------------------------------------------------------ holdout registry
    if layout.holdout_registry_path.exists():
        try:
            registry = HoldoutRegistry.model_validate(read_json(layout.holdout_registry_path))
        except (ValidationError, json.JSONDecodeError) as exc:
            rep.add("B040", "error", f"invalid holdout registry: {exc}".replace("\n", " "))
            registry = None
        if registry is not None:
            fps_outside = {lc.fingerprint: lc.case_id for lc in rep.cases.values()
                           if lc.split is not Split.HOLDOUT and lc.fingerprint}
            for e in registry.entries:
                lc = rep.cases.get(e.case_id)
                if lc is None:
                    rep.add("B040", "error", "registered holdout case is missing from the dataset", e.case_id)
                elif lc.split is not Split.HOLDOUT:
                    rep.add("B040", "error", f"registered holdout case now filed under {lc.split}", e.case_id)
                elif lc.fingerprint and lc.fingerprint != e.content_fingerprint:
                    rep.add("B040", "error", "holdout case content changed after registration", e.case_id)
                if e.content_fingerprint in fps_outside:
                    rep.add("B040", "error",
                            f"holdout content reused by non-holdout case {fps_outside[e.content_fingerprint]}",
                            e.case_id)
            unregistered = [c for c, lc in rep.cases.items() if lc.split is Split.HOLDOUT and c not in registry.ids()]
            for c in unregistered:
                rep.add("B040", "warning", "holdout case not yet in holdout registry", c)
    elif any(lc.split is Split.HOLDOUT for lc in rep.cases.values()):
        rep.add("B040", "warning", "holdout cases exist but no holdout registry has been written")

    # ------------------------------------------------------------------------------ id leakage
    for cid, lc in rep.cases.items():
        if lc.input is not None and lc.card is not None:
            hit = leaked_tokens(lc.input.call_id, lc.card.scenario, lc.card.category)
            if hit:
                rep.add("B010", "warning", f"call_id (evaluator-visible) contains scenario tokens {hit}", cid)
    return rep
