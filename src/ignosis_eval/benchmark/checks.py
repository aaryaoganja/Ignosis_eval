"""Benchmark checks (structure, splits, pairs, twins, gold references, leakage). Never mutates anything.

  B001 layout problem                                  B020 case card invalid / rule error (CCxxx/CXxxx/CPxxx)
  B003 duplicate item id across splits                 B022 case card missing
  B004 item.json missing or invalid                    B023 case card without an item (warning)
  B005 artifact missing                                B030 orphan gold file
  B006 transcript unparseable (§2.3: .txt/.json only)  B031 gold split / item id disagrees with the item
  B008 identical content in two splits (leakage)       B033 gold invalid or inconsistent with the rubric
  B009 identical content within a split (warning)      B034 gold missing (error with --require-gold)
  B011 pair / twin members in different splits (P-1 rule 1)
  B012 registry references an unknown item             B014 pair target_check is not a rubric check
  B013 pair transcripts differ in length by more than 10% (warning; authoring constraint 4, measured in words)
  B015 real (non-synthetic) item without the pii_reviewed tag on its card
  B017 identifier leak: a transcript contains an item id, a pair id or a rubric check id, or trips the P-17 pre-run
       payload test (item-ID pattern, pack/split word) — evaluators would see it and the run would fail (SC-05)
  B018 an item expected EVALUABLE has no AGENT or no BORROWER turn (DC-00 / DC-02 would make it NOT_EVALUABLE)
  B019 gold labels a G7 positive (BD-02: bench-a1 contains no G7 positive)
  B040 frozen DEV design (bench/public) problem (PDxxx rules, benchmark/public_dev.py)
  B041 a dev item disagrees with the frozen DEV design (id, pack, language, unit modes, tuning-only, header)
  B043 registries.json disagrees with the pairs / controls of the frozen DEV design
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from ignosis_eval.benchmark.case_card_rules import (
    RuleIssue,
    check_card_against_item,
    check_card_against_registries,
    check_card_rules,
    load_case_card_file,
)
from ignosis_eval.benchmark.layout import CARD_SUFFIX, GOLD_SUFFIX, SCOPE_SPLITS, BenchLayout
from ignosis_eval.benchmark.public_dev import DevDesign, PublicDevReport, validate_public_dev
from ignosis_eval.contracts.benchmark import ItemMeta
from ignosis_eval.contracts.case_card import CaseCard
from ignosis_eval.contracts.enums import GateId
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.contracts.io import read_json
from ignosis_eval.contracts.registries import Registries
from ignosis_eval.contracts.unit_alias import payload_violations
from ignosis_eval.golddrv.capability import CapabilityTable
from ignosis_eval.golddrv.derive import GoldDerivationError, derive_mode_gold
from ignosis_eval.integrity.freeze import content_fingerprint
from ignosis_eval.pipeline.intake import TranscriptParseError, parse_transcript
from ignosis_eval.pipeline.normalize import unit_facts
from ignosis_eval.spec.loader import Spec


class BenchCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class Issue:
    check_id: str
    severity: Literal["error", "warning"]
    message: str
    item_id: str | None = None

    def __str__(self) -> str:
        who = f"[{self.item_id}] " if self.item_id else ""
        return f"{self.severity.upper()} {self.check_id} {who}{self.message}"


@dataclass
class LoadedItem:
    meta: ItemMeta
    item_dir: Path
    card: CaseCard | None = None
    gold: GoldLabel | None = None
    words: int | None = None
    fingerprint: str | None = None


@dataclass
class BenchReport:
    issues: list[Issue] = field(default_factory=list)
    items: dict[str, LoadedItem] = field(default_factory=dict)
    public_dev: PublicDevReport | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_errors(self) -> None:
        if not self.ok:
            raise BenchCheckError("benchmark check failed:\n  " + "\n  ".join(str(i) for i in self.errors))


def load_registries(layout: BenchLayout) -> Registries:
    if not layout.registries_path.exists():
        return Registries()
    return Registries.model_validate(read_json(layout.registries_path))


def _from_rule(ri: RuleIssue) -> Issue:
    return Issue("B020", ri.severity, f"{ri.rule_id} {ri.message}", ri.item_id)


_PAIR_ID = re.compile(r"(?<![A-Za-z0-9-])(?:MP|CP|AP)-\d{2}(?![A-Za-z0-9])")


def _leak_pattern(tokens: set[str]) -> re.Pattern[str]:
    alts = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9-])(?:{alts})(?![A-Za-z0-9-])")


def _identifier_leaks(parsed, pattern: re.Pattern[str]) -> list[str]:
    found: set[str] = set()
    for t in parsed.turns:
        found |= set(pattern.findall(t.text)) | set(_PAIR_ID.findall(t.text))
        found |= {f"P-17 {v}" for v in payload_violations(t.text)}  # experiment-protocol P-17 rule 4
    return sorted(found)


def _design_issues(li: LoadedItem, parsed, design: DevDesign) -> list[Issue]:
    meta, iid = li.meta, li.meta.item_id
    d = design.items.get(iid)
    if d is None:
        return [Issue("B041", "error", "dev item is not in the frozen DEV design (bench/public); DEV items are fixed "
                                       "by frozen-contract §12 and the Stage 5 design", iid)]
    out = []
    for what, have, want in (("pack", meta.pack, d.pack), ("language", meta.language, d.language),
                             ("unit_modes", sorted(m.value for m in meta.unit_modes),
                              sorted(m.value for m in d.unit_modes)),
                             ("tuning_only", meta.tuning_only, d.tuning_only)):
        if have != want:
            out.append(Issue("B041", "error", f"{what} {have!r} but the frozen DEV design says {want!r}", iid))
    if parsed is not None and (parsed.header.call_start_ts is not None) != d.has_header:
        seen = "present" if parsed.header.call_start_ts is not None else "absent"
        wanted = "present" if d.has_header else "absent"
        out.append(Issue("B041", "error", f"transcript header call_start_ts {seen} but the design says {wanted} (G7)",
                         iid))
    return out


def check_bench(layout: BenchLayout, spec: Spec, *, scopes: tuple[str, ...] = ("dev",),
                require_gold: bool = False) -> BenchReport:
    rep = BenchReport()
    add = rep.issues.append
    try:
        registries = load_registries(layout)
    except (ValidationError, json.JSONDecodeError) as exc:
        add(Issue("B012", "error", f"registries.json invalid: {exc}"))
        registries = Registries()
    table = CapabilityTable.from_rubric(spec.rubric)
    design: DevDesign | None = None
    if "dev" in scopes and layout.public_dir.exists():  # the frozen DEV design; never needs BENCH_PRIVATE_DIR
        rep.public_dev = validate_public_dev(layout.public_dir, spec)
        rep.issues += [Issue("B040", i.severity, f"{i.rule_id} {i.message}", i.item_id) for i in rep.public_dev.issues]
        design = rep.public_dev.design
    leak_tokens = set(spec.registry.checks) | set(spec.registry.oos_codes) | registries.item_ids() | \
        (set(design.items) if design else set())
    for scope in scopes:
        for split in SCOPE_SPLITS[scope]:
            items, problems = layout.discover(split)
            rep.issues += [Issue("B001", "error", p) for p in problems]
            for ip in items:
                if ip.item_id in rep.items:
                    add(Issue("B003", "error", f"duplicate item id across splits ({split.value})", ip.item_id))
                    continue
                try:
                    meta = ItemMeta.model_validate(read_json(ip.meta_path))
                except (ValidationError, json.JSONDecodeError, FileNotFoundError) as exc:
                    add(Issue("B004", "error", f"item.json missing or invalid: {exc}", ip.item_id))
                    continue
                if meta.item_id != ip.item_id or meta.split is not split:
                    add(Issue("B004", "error", f"item.json says {meta.item_id}/{meta.split.value}", ip.item_id))
                    continue
                li = LoadedItem(meta, ip.item_dir)
                rep.items[meta.item_id] = li
                a = meta.artifacts
                missing = [r for r in (a.transcript, a.audio, a.platform_transcript) if r and not (ip.item_dir / r).exists()]
                for r in missing:
                    add(Issue("B005", "error", f"artifact missing: {r}", meta.item_id))
                parsed = None
                if a.transcript and a.transcript not in missing:
                    try:
                        parsed = parse_transcript(ip.item_dir / a.transcript)
                        li.words = sum(len(t.text.split()) for t in parsed.turns)
                    except (TranscriptParseError, OSError) as exc:
                        add(Issue("B006", "error", f"transcript unparseable: {exc}", meta.item_id))
                if parsed is not None:
                    leaks = _identifier_leaks(parsed, _leak_pattern(leak_tokens | {meta.item_id}))
                    if leaks:
                        add(Issue("B017", "error", f"transcript text contains identifiers {leaks}: evaluators would "
                                                   "see them (item ids, pair ids, check ids, pack/split words never "
                                                   "appear in a transcript; P-17 fails the run)", meta.item_id))
                    expect_evaluable = design is not None and meta.item_id in design.items and \
                        design.items[meta.item_id].evaluability == "EVALUABLE"
                    roles = {t.role.value for t in parsed.turns}
                    if expect_evaluable and not {"AGENT", "BORROWER"} <= roles:
                        add(Issue("B018", "error", f"expected EVALUABLE but the transcript has roles {sorted(roles)}: "
                                                   "without an AGENT turn DC-00 gives ROLE_UNCERTAIN, without a "
                                                   "BORROWER turn DC-02 gives NON_CONVERSATIONAL (SC-03); a third "
                                                   "party on the customer side is labeled BORROWER (rubric 1.2 "
                                                   "event_common.turn: speaker matches the event side)",
                                          meta.item_id))
                if design is not None and split.value == "dev":
                    rep.issues += _design_issues(li, parsed, design)
                if not missing:
                    li.fingerprint = content_fingerprint(meta, ip.item_dir)
                card, cissues = load_case_card_file(ip.card_path) if ip.card_path.exists() else (None, [])
                if not ip.card_path.exists():
                    add(Issue("B022", "error", "case card missing", meta.item_id))
                rep.issues += [_from_rule(i) for i in cissues]
                if card is not None:
                    li.card = card
                    rep.issues += [_from_rule(i) for i in check_card_rules(card, spec.registry)]
                    rep.issues += [_from_rule(i) for i in check_card_against_item(
                        card, meta, n_turns=len(parsed.turns) if parsed else None,
                        has_call_start_ts=(parsed.header.call_start_ts is not None) if parsed else None,
                        truncated=parsed.header.truncated_start if parsed else None)]
                    rep.issues += [_from_rule(i) for i in check_card_against_registries(card, registries)]
                    if not meta.synthetic and "pii_reviewed" not in card.tags:
                        add(Issue("B015", "error", "real (non-synthetic) items need the pii_reviewed tag", meta.item_id))
                if ip.gold_path.exists():
                    try:
                        gold = GoldLabel.model_validate(read_json(ip.gold_path))
                    except (ValidationError, json.JSONDecodeError) as exc:
                        add(Issue("B033", "error", f"gold invalid: {exc}", meta.item_id))
                        gold = None
                    if gold is not None:
                        li.gold = gold
                        g7 = gold.gates[GateId.G7]
                        if g7.status.value == "FAIL" or (g7.status.value == "INCONCLUSIVE" and g7.trigger):
                            add(Issue("B019", "error", "gold labels a G7 positive, but BD-02 states that "
                                                       "bench-a1 contains no G7 positive (a new BD-xx changelog "
                                                       "entry is needed)", meta.item_id))
                        if gold.item_id != meta.item_id or gold.split is not meta.split:
                            add(Issue("B031", "error", "gold item id / split disagrees with the item", meta.item_id))
                        elif not missing and (parsed is not None or not a.transcript):
                            try:
                                for facts in unit_facts(meta, ip.item_dir):
                                    derive_mode_gold(gold, facts, spec.rubric, table)
                            except (GoldDerivationError, TranscriptParseError, OSError) as exc:
                                add(Issue("B033", "error", f"gold inconsistent with the rubric: {exc}", meta.item_id))
                elif require_gold and meta.scoring_role != "never":
                    add(Issue("B034", "error", "gold missing", meta.item_id))
        # orphans
        for iid, _p in layout.discover_suffixed(layout.cards_dir(scope), CARD_SUFFIX)[0]:
            if iid not in rep.items:
                add(Issue("B023", "warning", "case card without an item", iid))
        gold_found, probs = layout.discover_suffixed(layout.gold_dir(scope), GOLD_SUFFIX)
        rep.issues += [Issue("B001", "error", p) for p in probs]
        for iid, _p in gold_found:
            if iid not in rep.items:
                add(Issue("B030", "error", "gold file without an item", iid))

    # leakage (P-1 rule 1 / §13) — across every loaded split
    by_fp: dict[str, list[LoadedItem]] = {}
    for li in rep.items.values():
        if li.fingerprint:
            by_fp.setdefault(li.fingerprint, []).append(li)
    for group in by_fp.values():
        if len(group) > 1:
            splits = {g.meta.split for g in group}
            ids = sorted(g.meta.item_id for g in group)
            add(Issue("B008" if len(splits) > 1 else "B009", "error" if len(splits) > 1 else "warning",
                      f"identical content in {ids}"))
    # registries
    known = set(rep.items)
    for p in registries.pairs:
        for iid in (p.clean_item, p.violating_item):
            if iid not in known:
                add(Issue("B012", "warning", f"pair {p.pair_id} references {iid}, not present in the checked scopes"))
        if p.target_check not in spec.registry.checks:
            add(Issue("B014", "error", f"pair {p.pair_id} target_check {p.target_check} is not a rubric check"))
        ca, vb = rep.items.get(p.clean_item), rep.items.get(p.violating_item)
        if ca and vb:
            if ca.meta.split is not vb.meta.split:
                add(Issue("B011", "error", f"pair {p.pair_id} crosses splits"))
            if ca.words and vb.words and abs(ca.words - vb.words) > 0.10 * max(ca.words, vb.words):
                add(Issue("B013", "warning", f"pair {p.pair_id} lengths differ by more than 10% "
                                             f"({ca.words} vs {vb.words} words)"))
    for t in registries.twins:
        ta, tb = rep.items.get(t.base_item), rep.items.get(t.twin_item)
        if ta is None or tb is None:
            add(Issue("B012", "warning", f"twin {t.twin_id} references an item not present in the checked scopes"))
        elif ta.meta.split is not tb.meta.split:
            add(Issue("B011", "error", f"twin {t.twin_id} crosses splits"))
    for c in registries.controls:
        if c.item_id not in known:
            add(Issue("B012", "warning", f"control {c.item_id} not present in the checked scopes"))
    if design is not None:  # a populated registry must agree with the frozen DEV design
        want = design.registries()
        want_pairs = {p.pair_id: p for p in want.pairs}
        want_controls = {c.item_id: sorted(g.value for g in c.target_gates) for c in want.controls}
        for p in registries.pairs:
            if {p.clean_item, p.violating_item} & set(design.items) and want_pairs.get(p.pair_id) != p:
                add(Issue("B043", "error", f"pair {p.pair_id} disagrees with the frozen DEV design "
                                           f"{want_pairs.get(p.pair_id)}"))
        for c in registries.controls:
            if c.item_id in design.items and sorted(g.value for g in c.target_gates) != want_controls.get(c.item_id):
                add(Issue("B043", "error", f"control {c.item_id} target gates disagree with the frozen DEV design "
                                           f"{want_controls.get(c.item_id)}", c.item_id))
    return rep
