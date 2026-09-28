"""Case-card validation rules, reconciled with rubric 1.0-mvp and the B-01 authoring constraints.

Every rule has a stable id (docs/benchmark-authoring.md). `error` blocks the item (bench checks fail, gold
freeze refuses); `warning` is reported for human review. Cards are written by the human case author; the
implementing agent never writes card content (implementation-blockers, authoring constraint 1).

  CC001 card schema-invalid                      CC008 approved card needs a reviewer other than the author
  CC002 unresolved placeholder / TODO / TBD      CC009 LLM-assisted drafting must name the model family
  CC003 rationale shorter than 40 characters     CC010 dangerous_win requires a positive outcome
  CC004 target_check not a rubric check          CC011 clean_loss requires a non-positive outcome
  CC005 clean card carries defect fields         CC012 NOT_EVALUABLE expectation needs reason codes
  CC006 severity / repair inconsistent w/ rubric CC013 pack / split mismatch (red team)
  CC007 defect without evidence turns (G7: header) CC014 always-OUT_OF_SCOPE target carries a severity
  CX001 card disagrees with item.json (split, pack, language, unit modes)
  CX002 evidence turn beyond the transcript length
  CX003 G7 target without a call_start_ts header (authoring constraint 7)
  CX004 truncation expectation without truncated_start header or in-text marker (constraint 6)
  CP001 pair membership disagrees with the pair registry     CP002 control target gates disagree with registry
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from ignosis_eval.contracts.benchmark import ItemMeta
from ignosis_eval.contracts.case_card import CardStatus, CaseCard
from ignosis_eval.contracts.enums import EvaluabilityStatus, Pack, RepairStatus, Split
from ignosis_eval.contracts.io import read_yaml
from ignosis_eval.contracts.registries import Registries
from ignosis_eval.spec.registry import Registry

PLACEHOLDER_RE = re.compile(r"<[^<>\n]{2,}>|\bTODO\b|\bTBD\b|\bFIXME\b")
MIN_RATIONALE_CHARS = 40


@dataclass(frozen=True)
class RuleIssue:
    rule_id: str
    severity: Literal["error", "warning"]
    message: str
    item_id: str | None = None

    def __str__(self) -> str:
        who = f"[{self.item_id}] " if self.item_id else ""
        return f"{self.severity.upper()} {self.rule_id} {who}{self.message}"


def _err(rule: str, msg: str, iid: str | None = None) -> RuleIssue:
    return RuleIssue(rule, "error", msg, iid)


def _warn(rule: str, msg: str, iid: str | None = None) -> RuleIssue:
    return RuleIssue(rule, "warning", msg, iid)


def _placeholders(obj: Any, path: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(obj, str) and PLACEHOLDER_RE.search(obj):
        out.append(path or "<root>")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out += _placeholders(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _placeholders(v, f"{path}[{i}]")
    return out


def parse_case_card(data: Any) -> tuple[CaseCard | None, list[RuleIssue]]:
    iid = data.get("item_id") if isinstance(data, dict) else None
    issues = [_err("CC002", f"unresolved placeholder at {p}", iid) for p in _placeholders(data)]
    try:
        return CaseCard.model_validate(data), issues
    except ValidationError as exc:
        return None, issues + [_err("CC001", f"schema-invalid: {exc.errors()[:3]}", iid)]


def load_case_card_file(path: str | Path) -> tuple[CaseCard | None, list[RuleIssue]]:
    try:
        return parse_case_card(read_yaml(path))
    except OSError as exc:
        return None, [_err("CC001", f"unreadable card {path}: {exc}")]


def check_card_rules(card: CaseCard, registry: Registry) -> list[RuleIssue]:
    iid, out = card.item_id, []
    if len(card.rationale.strip()) < MIN_RATIONALE_CHARS:
        out.append(_err("CC003", f"rationale shorter than {MIN_RATIONALE_CHARS} characters", iid))
    t = card.target_check
    oos = set(registry.oos_codes)
    if t is not None and t not in registry.checks and t not in oos:
        out.append(_err("CC004", f"target_check {t!r} is not a rubric check", iid))
    if t is None:
        if card.severity is not None or card.repair_status is not None or card.evidence_turns:
            out.append(_err("CC005", "a clean card (target_check null) carries severity / repair / evidence", iid))
    elif t in oos:
        if card.severity is not None:
            out.append(_err("CC014", f"{t} is always OUT_OF_SCOPE and cannot carry a defect severity", iid))
    elif t in registry.checks and card.severity is not None:
        cd = registry.get(t)
        if cd.is_gate:
            if card.severity.value != "CRITICAL" or card.repair_status not in (None, RepairStatus.UNREPAIRED):
                out.append(_err("CC006", "gates are CRITICAL and never repairable", iid))
        elif cd.severity is not None and not cd.severity_rule:
            expected = cd.severity.value
            if card.repair_status is RepairStatus.REPAIRED:
                expected = {"MAJOR": "MINOR", "MINOR": "INFORMATIONAL"}.get(expected, expected)
            if card.severity.value != expected:
                out.append(_err("CC006", f"severity {card.severity.value} != rubric post-repair {expected}", iid))
        if not card.evidence_turns and not (t == "G7" and card.evidence_header):
            out.append(_err("CC007", "a defect card needs evidence turns (G7: evidence_header)", iid))
    if card.status is CardStatus.APPROVED and not [r for r in card.authoring.reviewers if r != card.authoring.author_id]:
        out.append(_err("CC008", "approved cards need a reviewer other than the author", iid))
    if card.authoring.llm_assisted and not card.authoring.llm_family:
        out.append(_err("CC009", "LLM-assisted drafting must record the model family (B-01 constraint 1)", iid))
    if card.dangerous_win.value != "NONE" and not card.outcome.positive:
        out.append(_err("CC010", "dangerous_win requires a positive outcome", iid))
    if card.clean_loss and card.outcome.positive:
        out.append(_err("CC011", "clean_loss requires a non-positive outcome", iid))
    if card.expected_evaluability is EvaluabilityStatus.NOT_EVALUABLE and not card.expected_reason_codes:
        out.append(_err("CC012", "a NOT_EVALUABLE expectation needs reason codes", iid))
    if (card.pack is Pack.REDTEAM) != (card.split is Split.REDTEAM):
        out.append(_err("CC013", "red-team items (and only they) belong to the redteam split", iid))
    return out


def check_card_against_item(card: CaseCard, meta: ItemMeta, *, n_turns: int | None, has_call_start_ts: bool | None,
                            truncated: bool | None) -> list[RuleIssue]:
    iid, out = card.item_id, []
    for field in ("item_id", "split", "pack", "language"):
        if getattr(card, field) != getattr(meta, field):
            out.append(_err("CX001", f"card {field}={getattr(card, field)} but item.json says {getattr(meta, field)}",
                            iid))
    if sorted(card.unit_modes) != sorted(meta.unit_modes):
        out.append(_err("CX001", "card unit_modes differ from item.json", iid))
    if n_turns is not None and any(t > n_turns for t in card.evidence_turns):
        out.append(_err("CX002", f"evidence turn beyond the transcript's {n_turns} turns", iid))
    if card.target_check == "G7" and has_call_start_ts is False:
        out.append(_err("CX003", "G7 items must carry a call_start_ts header (authoring constraint 7)", iid))
    if "TRANSCRIPT_TRUNCATED" in [r.value for r in card.expected_reason_codes] and truncated is False:
        out.append(_err("CX004", "truncation needs truncated_start: true or an in-text marker (constraint 6)", iid))
    return out


def check_card_against_registries(card: CaseCard, reg: Registries) -> list[RuleIssue]:
    iid, out = card.item_id, []
    pairs = [p for p in reg.pairs if iid in (p.clean_item, p.violating_item)]
    if card.pair is None and pairs:
        out.append(_err("CP001", f"item is in pair registry {pairs[0].pair_id} but the card has no pair", iid))
    if card.pair is not None:
        p = next((x for x in reg.pairs if x.pair_id == card.pair.pair_id), None)
        want = None if p is None else (p.clean_item if card.pair.role == "clean" else p.violating_item)
        other = None if p is None else (p.violating_item if card.pair.role == "clean" else p.clean_item)
        if p is None or want != iid or other != card.pair.counterpart_item_id:
            out.append(_err("CP001", f"pair membership {card.pair.pair_id}/{card.pair.role} disagrees with registry",
                            iid))
    targets = sorted(g.value for g in reg.control_targets(iid))
    if sorted(g.value for g in card.control_target_gates) != targets:
        out.append(_err("CP002", f"control target gates {card.control_target_gates} != registry {targets}", iid))
    return out
