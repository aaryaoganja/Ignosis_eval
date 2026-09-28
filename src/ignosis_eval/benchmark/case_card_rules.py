"""Case-card validation rules.

Every rule has a stable id; docs/benchmark-authoring.md lists them. Severity `error` blocks the case
(benchmark checks fail, gold freeze refuses); `warning` is reported for human review.

Usage:
    python scripts/validate_case_cards.py benchmark/case_cards/dev/some-case.card.yaml
    ignosis-eval casecard validate <paths...> [--benchmark-root benchmark] [--profile ...]
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.case_card import CardSeverity, CaseCard
from ignosis_eval.contracts.enums import (
    AttributionTarget,
    Capability,
    EvaluabilityStatus,
    InputMode,
    OutcomeClass,
    RepairStatus,
)
from ignosis_eval.contracts.profile import Profile

PLACEHOLDER_RE = re.compile(r"<[^<>\n]{2,}>|\bTODO\b|\bTBD\b|\bFIXME\b")
# Generic words that may appear in ids without revealing anything about the scenario.
LEAKAGE_STOPWORDS = frozenset({"call", "calls", "case", "agent", "customer", "test", "fixture"})


def leaked_tokens(identifier: str, *texts: str) -> list[str]:
    """Scenario/category tokens (>= 4 chars, not stopwords) that appear inside an identifier."""
    toks = {t for txt in texts for t in re.split(r"[^a-z0-9]+", txt.lower()) if len(t) >= 4}
    return sorted(t for t in toks - LEAKAGE_STOPWORDS if t in identifier.lower())
MIN_RATIONALE_CHARS = 40


@dataclass(frozen=True)
class RuleIssue:
    rule_id: str
    severity: Literal["error", "warning"]
    message: str
    case_id: str | None = None

    def __str__(self) -> str:
        who = f"[{self.case_id}] " if self.case_id else ""
        return f"{self.severity.upper()} {self.rule_id} {who}{self.message}"


def _err(rule: str, msg: str, cid: str | None = None) -> RuleIssue:
    return RuleIssue(rule, "error", msg, cid)


def _warn(rule: str, msg: str, cid: str | None = None) -> RuleIssue:
    return RuleIssue(rule, "warning", msg, cid)


def _placeholders(obj: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, str):
        if PLACEHOLDER_RE.search(obj):
            hits.append(path or "<root>")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            hits += _placeholders(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += _placeholders(v, f"{path}[{i}]")
    return hits


def parse_case_card(data: Any) -> tuple[CaseCard | None, list[RuleIssue]]:
    cid = data.get("case_id") if isinstance(data, dict) else None
    cid = cid if isinstance(cid, str) else None
    issues = [_err("CC001", f"placeholder text remains at {p}", cid) for p in _placeholders(data)]
    try:
        card = CaseCard.model_validate(data)
    except ValidationError as exc:
        for e in exc.errors():
            loc = ".".join(str(x) for x in e["loc"]) or "<root>"
            issues.append(_err("CC000", f"{loc}: {e['msg']}", cid))
        return None, issues
    return card, issues


def check_card_rules(card: CaseCard) -> list[RuleIssue]:
    c, cid = card, card.case_id
    out: list[RuleIssue] = []
    clean = c.target_defect is None
    if clean:
        if c.severity is not CardSeverity.NONE:
            out.append(_err("CC010", "no target_defect: severity must be 'none'", cid))
        if c.gate is not None:
            out.append(_err("CC010", "no target_defect: gate must be null", cid))
        if c.evidence_turns or c.evidence_metadata_fields or c.evidence_audio_spans_ms:
            out.append(_err("CC010", "no target_defect: evidence fields must be empty", cid))
        if c.attribution is not None:
            out.append(_err("CC010", "no target_defect: attribution must be null", cid))
        if c.repair_status is not RepairStatus.NOT_APPLICABLE:
            out.append(_err("CC010", "no target_defect: repair_status must be 'not_applicable'", cid))
    else:
        if c.severity is CardSeverity.NONE:
            out.append(_err("CC011", "target_defect set: severity must not be 'none'", cid))
        if not (c.evidence_turns or c.evidence_metadata_fields or c.evidence_audio_spans_ms):
            out.append(_err("CC011", "target_defect set: cite evidence (evidence_turns, "
                                     "evidence_metadata_fields or evidence_audio_spans_ms)", cid))
        if c.attribution is None:
            out.append(_err("CC011", "target_defect set: attribution is required", cid))
        if c.repair_status is RepairStatus.NOT_APPLICABLE:
            out.append(_err("CC014", "target_defect set: repair_status must describe the repair state", cid))
    if c.gate is not None and clean:
        out.append(_err("CC012", "gate set without a target_defect", cid))
    if c.gate is not None and c.severity is not CardSeverity.CRITICAL:
        out.append(_warn("CC013", "gate set but severity is not 'critical' (gates are treated as critical)", cid))

    if c.dangerous_win:
        if c.outcome.outcome_class is not OutcomeClass.WIN:
            out.append(_err("CC020", "dangerous_win requires outcome_class 'win'", cid))
        if c.severity not in (CardSeverity.CRITICAL, CardSeverity.MAJOR):
            out.append(_err("CC020", "dangerous_win requires a critical or major target defect", cid))
        if c.repair_status is RepairStatus.REPAIRED:
            out.append(_err("CC020", "dangerous_win cannot have a fully repaired defect", cid))
    if c.clean_loss:
        if c.outcome.outcome_class is not OutcomeClass.LOSS:
            out.append(_err("CC021", "clean_loss requires outcome_class 'loss'", cid))
        if c.severity not in (CardSeverity.NONE, CardSeverity.MINOR):
            out.append(_err("CC021", "clean_loss cannot carry a critical or major target defect", cid))
    if c.dangerous_win and c.clean_loss:
        out.append(_err("CC022", "dangerous_win and clean_loss are mutually exclusive", cid))
    if c.expected_evaluability is not EvaluabilityStatus.EVALUABLE and (c.dangerous_win or c.clean_loss):
        out.append(_err("CC023", "non-evaluable cases cannot be dangerous_win/clean_loss", cid))

    if len(c.rationale.strip()) < MIN_RATIONALE_CHARS:
        out.append(_err("CC030", f"rationale must explain why the case exists (>= {MIN_RATIONALE_CHARS} chars)", cid))
    if not clean and c.intended_behavior.strip() == c.agent_behavior.strip():
        out.append(_warn("CC031", "defect case with identical intended_behavior and agent_behavior", cid))

    if c.minimal_pair is not None:
        if c.minimal_pair.counterpart_case_id == cid:
            out.append(_err("CC040", "minimal_pair.counterpart_case_id cannot be the case itself", cid))
        if not c.minimal_pair.held_constant:
            out.append(_err("CC041", "minimal_pair.held_constant must list what is held constant", cid))
    if c.attribution_pair is not None:
        cps = c.attribution_pair.counterpart_case_ids
        if not cps or cid in cps:
            out.append(_err("CC050", "attribution_pair.counterpart_case_ids must be non-empty and exclude self", cid))
    if c.attribution is not None and c.attribution.determinable == (c.attribution.target is AttributionTarget.UNDETERMINED):
        out.append(_err("CC051", "attribution.determinable must be false iff target is 'undetermined'", cid))

    caps = c.modality.capability_boundaries
    if (
        c.modality.intended_modality is InputMode.TRANSCRIPT_ONLY
        and Capability.AUDIO in caps.required_capabilities
        and c.expected_evaluability is EvaluabilityStatus.EVALUABLE
    ):
        out.append(_warn("CC060", "transcript_only case requires audio yet is expected to be fully evaluable", cid))
    if c.modality.intended_modality is InputMode.TRANSCRIPT_ONLY and not caps.transcript_sufficient and \
            c.expected_evaluability is EvaluabilityStatus.EVALUABLE:
        out.append(_warn("CC061", "transcript_only case marked transcript_sufficient=false but expected evaluable", cid))
    if c.modality.intended_modality is InputMode.AUDIO_ONLY and not caps.audio_sufficient and \
            c.expected_evaluability is EvaluabilityStatus.EVALUABLE:
        out.append(_warn("CC061", "audio_only case marked audio_sufficient=false but expected evaluable", cid))

    leaked = leaked_tokens(cid, c.scenario, c.category)
    if leaked:
        out.append(_warn("CC070", f"case_id contains scenario/category tokens {leaked} (identifier leakage)", cid))

    if c.status.value == "approved":
        others = [r for r in c.authoring.reviewers if r != c.authoring.author_id]
        if not others:
            out.append(_err("CC080", "approved cards need at least one reviewer other than the author", cid))
    if not c.source_kind.is_synthetic and "pii_reviewed" not in c.tags:
        out.append(_err("CC090", "real (non-synthetic) cases must carry the 'pii_reviewed' tag", cid))
    return out


def check_card_against_input(card: CaseCard, inp: CanonicalInput) -> list[RuleIssue]:
    cid = card.case_id
    out: list[RuleIssue] = []
    if card.modality.intended_modality is not inp.input_mode:
        out.append(_err("CX02", f"intended_modality={card.modality.intended_modality} but input_mode={inp.input_mode}", cid))
    if card.language != inp.language.primary:
        out.append(_err("CX03", f"card language {card.language} != input language {inp.language.primary}", cid))
    turns = inp.turn_map()
    if turns:
        missing = [t for t in card.evidence_turns if t not in turns]
        if missing:
            out.append(_err("CX01", f"evidence_turns not in input transcript: {missing}", cid))
    elif card.evidence_turns:
        out.append(_warn("CX01", "evidence_turns cannot be verified: input has no transcript", cid))
    return out


def check_card_against_profile(card: CaseCard, profile: Profile) -> list[RuleIssue]:
    cid = card.case_id
    out: list[RuleIssue] = []
    if card.target_defect is not None:
        dd = profile.defect_def(card.target_defect.defect_id)
        if dd is None:
            out.append(_err("CP01", f"target defect {card.target_defect.defect_id} not in profile {profile.profile_id}", cid))
        else:
            if card.gate is not None and dd.gate_id != card.gate:
                out.append(_err("CP03", f"defect {dd.defect_id} belongs to gate {dd.gate_id}, card says {card.gate}", cid))
            if dd.severity.value != card.severity.value:
                out.append(_err("CP04", f"profile severity of {dd.defect_id} is {dd.severity}, card says {card.severity}", cid))
    if card.gate is not None and profile.gate_def(card.gate) is None:
        out.append(_err("CP02", f"gate {card.gate} not in profile {profile.profile_id}", cid))
    for inc in card.incidental_defects:
        if profile.defect_def(inc.defect_id) is None:
            out.append(_err("CP01", f"incidental defect {inc.defect_id} not in profile", cid))
    return out


def check_card_set(cards: list[CaseCard]) -> list[RuleIssue]:
    out: list[RuleIssue] = []
    by_id: dict[str, CaseCard] = {}
    for c in cards:
        if c.case_id in by_id:
            out.append(_err("CS07", "duplicate case_id across case cards", c.case_id))
        by_id[c.case_id] = c

    pairs: dict[str, list[CaseCard]] = defaultdict(list)
    for c in cards:
        if c.minimal_pair:
            pairs[c.minimal_pair.pair_id].append(c)
    for pid, members in pairs.items():
        if len(members) != 2:
            out.append(_err("CS05", f"minimal pair {pid} has {len(members)} members (need exactly 2)"))
            continue
        a, b = members
        if a.split is not b.split:
            out.append(_err("CS04", f"minimal pair {pid} crosses splits ({a.split} vs {b.split})"))
        if a.minimal_pair.role == b.minimal_pair.role:
            out.append(_err("CS03", f"minimal pair {pid} members share role {a.minimal_pair.role!r}"))
        if a.minimal_pair.counterpart_case_id != b.case_id or b.minimal_pair.counterpart_case_id != a.case_id:
            out.append(_err("CS02", f"minimal pair {pid} counterparts are not reciprocal"))
    for c in cards:
        if c.minimal_pair and c.minimal_pair.counterpart_case_id not in by_id:
            out.append(_err("CS01", f"minimal pair counterpart {c.minimal_pair.counterpart_case_id} not found", c.case_id))

    apairs: dict[str, list[CaseCard]] = defaultdict(list)
    for c in cards:
        if c.attribution_pair:
            apairs[c.attribution_pair.attribution_pair_id].append(c)
    for apid, members in apairs.items():
        if len(members) < 2:
            out.append(_err("CS06", f"attribution pair {apid} has fewer than 2 members"))
        if len({m.split for m in members}) > 1:
            out.append(_err("CS06", f"attribution pair {apid} crosses splits"))
        ids = {m.case_id for m in members}
        for m in members:
            if set(m.attribution_pair.counterpart_case_ids) != ids - {m.case_id}:
                out.append(_err("CS06", f"attribution pair {apid} counterpart lists are inconsistent", m.case_id))
    return out


def load_case_card_file(path: str | Path) -> tuple[CaseCard | None, list[RuleIssue]]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, [_err("CC000", f"invalid YAML: {exc}")]
    if not isinstance(data, dict):
        return None, [_err("CC000", "case card must be a YAML mapping")]
    card, issues = parse_case_card(data)
    if card is not None:
        issues += check_card_rules(card)
    return card, issues


def validate_case_card_files(
    paths: list[Path], profile: Profile | None = None, input_resolver=None, set_checks: bool = False
) -> list[RuleIssue]:
    """Validate cards individually and, if `set_checks`, as a set (pairs, duplicates).

    `input_resolver(card) -> CanonicalInput | None` enables the card-vs-input checks (CX*).
    Set checks assume `paths` is the complete set of cards (e.g. a whole benchmark).
    """
    issues: list[RuleIssue] = []
    cards: list[CaseCard] = []
    for p in paths:
        card, card_issues = load_case_card_file(p)
        issues += [RuleIssue(i.rule_id, i.severity, f"{Path(p).name}: {i.message}", i.case_id) for i in card_issues]
        if card is None:
            continue
        cards.append(card)
        if profile is not None:
            issues += check_card_against_profile(card, profile)
        if input_resolver is not None:
            inp = input_resolver(card)
            if inp is not None:
                issues += check_card_against_input(card, inp)
    if set_checks:
        issues += check_card_set(cards)
    return issues
