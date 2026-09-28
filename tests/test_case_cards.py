"""Case-card template and validation rules."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from conftest import FIXTURE_ROOT
from factories import PROFILE, PROFILE_PATH
from ignosis_eval.benchmark.case_card_rules import (
    check_card_against_profile,
    check_card_rules,
    check_card_set,
    load_case_card_file,
    parse_case_card,
)
from ignosis_eval.cli import main

TEMPLATE = Path(__file__).resolve().parents[1] / "benchmark" / "templates" / "case_card.template.yaml"
CARD_DEFECT = FIXTURE_ROOT / "case_cards" / "dev" / "fx-0002.card.yaml"   # critical defect, dangerous win
CARD_CLEAN = FIXTURE_ROOT / "case_cards" / "dev" / "fx-0001.card.yaml"


def _raw(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _rules(data: dict) -> set[str]:
    card, issues = parse_case_card(data)
    ids = {i.rule_id for i in issues if i.severity == "error"}
    if card is not None:
        ids |= {i.rule_id for i in check_card_rules(card) if i.severity == "error"}
    return ids


def test_template_is_rejected_until_filled():
    card, issues = load_case_card_file(TEMPLATE)
    assert card is None
    assert "CC001" in {i.rule_id for i in issues}


def test_fixture_cards_are_valid():
    for path in sorted((FIXTURE_ROOT / "case_cards").rglob("*.card.yaml")):
        card, issues = load_case_card_file(path)
        assert card is not None, path
        assert [i for i in issues if i.severity == "error"] == [], (path, issues)
        assert check_card_against_profile(card, PROFILE) == []


def test_template_covers_every_case_card_field():
    from ignosis_eval.contracts.case_card import CaseCard

    template_keys = set(yaml.safe_load(TEMPLATE.read_text(encoding="utf-8")))
    assert template_keys == set(CaseCard.model_fields)


@pytest.mark.parametrize("field", ["rationale", "ambiguity_notes", "evidence_turns", "customer_context",
                                   "intended_behavior", "agent_behavior", "target_defect", "gate", "severity",
                                   "repair_status", "attribution", "outcome", "dangerous_win", "clean_loss",
                                   "modality", "minimal_pair", "scenario"])
def test_missing_required_field_is_an_error(field):
    data = _raw(CARD_DEFECT)
    del data[field]
    assert "CC000" in _rules(data)


def _mut(path, **changes):
    data = copy.deepcopy(_raw(path))
    for dotted, value in changes.items():
        cur = data
        keys = dotted.split("__")
        for k in keys[:-1]:
            cur = cur[k]
        cur[keys[-1]] = value
    return data


@pytest.mark.parametrize("path,changes,rule", [
    (CARD_CLEAN, {"severity": "critical"}, "CC010"),
    (CARD_CLEAN, {"gate": "G_NO_THREATS_OR_ABUSE"}, "CC012"),
    (CARD_DEFECT, {"evidence_turns": []}, "CC011"),
    (CARD_DEFECT, {"attribution": None}, "CC011"),
    (CARD_DEFECT, {"repair_status": "not_applicable"}, "CC014"),
    (CARD_DEFECT, {"outcome__outcome_class": "loss"}, "CC020"),
    (CARD_DEFECT, {"repair_status": "repaired"}, "CC020"),
    (CARD_DEFECT, {"clean_loss": True}, "CC021"),
    (CARD_DEFECT, {"clean_loss": True}, "CC022"),
    (CARD_DEFECT, {"expected_evaluability": "inconclusive"}, "CC023"),
    (CARD_DEFECT, {"rationale": "too short"}, "CC030"),
    (CARD_DEFECT, {"minimal_pair__counterpart_case_id": "fx-0002"}, "CC040"),
    (CARD_DEFECT, {"minimal_pair__held_constant": []}, "CC041"),
    (CARD_DEFECT, {"attribution__determinable": False}, "CC051"),
    (CARD_DEFECT, {"authoring__reviewers": ["fixture-author"]}, "CC080"),
    (CARD_DEFECT, {"source_kind": "real_redacted"}, "CC090"),
    (CARD_DEFECT, {"agent_behavior": "TODO"}, "CC001"),
])
def test_rules(path, changes, rule):
    assert rule in _rules(_mut(path, **changes))


def test_rule_ids_absent_on_valid_card():
    assert _rules(_raw(CARD_DEFECT)) == set()


def test_profile_cross_checks():
    card, _ = parse_case_card(_mut(CARD_DEFECT, target_defect__defect_id="DEF_UNKNOWN"))
    assert "CP01" in {i.rule_id for i in check_card_against_profile(card, PROFILE)}
    card, _ = parse_case_card(_mut(CARD_DEFECT, gate="G_CONTACT_HOURS"))
    assert "CP03" in {i.rule_id for i in check_card_against_profile(card, PROFILE)}
    card, _ = parse_case_card(_mut(CARD_DEFECT, severity="major"))
    assert "CP04" in {i.rule_id for i in check_card_against_profile(card, PROFILE)}


def test_pair_set_checks():
    a, _ = parse_case_card(_raw(CARD_CLEAN))
    b, _ = parse_case_card(_mut(CARD_DEFECT, split="holdout"))
    rules = {i.rule_id for i in check_card_set([a, b])}
    assert "CS04" in rules  # pair crosses splits
    b2, _ = parse_case_card(_mut(CARD_DEFECT, minimal_pair__role="control"))
    assert "CS03" in {i.rule_id for i in check_card_set([a, b2])}
    assert "CS05" in {i.rule_id for i in check_card_set([a])}


def test_validate_script_exit_codes(capsys):
    assert main(["casecard", "validate", str(CARD_DEFECT), "--profile", str(PROFILE_PATH)]) == 0
    assert main(["casecard", "validate", str(TEMPLATE)]) == 1

