"""Case-card rules (CCxxx) reconciled with rubric 1.2-mvp and the B-01 authoring constraints."""

from __future__ import annotations

import factories as F
from ignosis_eval.benchmark.case_card_rules import check_card_rules, parse_case_card


def _ids(card_dict, spec):
    card, issues = parse_case_card(card_dict)
    if card is not None:
        issues += check_card_rules(card, spec.registry)
    return {i.rule_id for i in issues if i.severity == "error"}


def test_clean_stub_card_is_valid(spec):
    assert _ids(F.card("ZZ-C01"), spec) == set()


def test_rules(spec):
    assert "CC002" in _ids(F.card("ZZ-C01", scenario="<fill me in>"), spec)
    assert "CC001" in _ids({**F.card("ZZ-C01"), "unknown_field": 1}, spec)
    assert "CC003" in _ids(F.card("ZZ-C01", rationale="short"), spec)
    assert "CC004" in _ids(F.card("ZZ-C01", target_check="XYZ-99", severity="MAJOR",
                                  repair_status="UNREPAIRED", evidence_turns=[1]), spec)
    assert "CC005" in _ids(F.card("ZZ-C01", evidence_turns=[2]), spec)
    assert "CC006" in _ids(F.card("ZZ-C01", target_check="G3", severity="MAJOR", repair_status="UNREPAIRED",
                                  evidence_turns=[2]), spec)
    assert "CC006" in _ids(F.card("ZZ-C01", target_check="G3", severity="CRITICAL", repair_status="REPAIRED",
                                  evidence_turns=[2]), spec)  # gates are never repairable
    assert "CC006" not in _ids(F.card("ZZ-C01", target_check="ACC-05", severity="MINOR", repair_status="REPAIRED",
                                      evidence_turns=[2]), spec)  # repaired Major -> Minor (AJ-08)
    assert "CC007" in _ids(F.card("ZZ-C01", target_check="UND-01", severity="MAJOR", repair_status="UNREPAIRED"),
                           spec)
    assert "CC007" not in _ids(F.card("ZZ-C01", target_check="G7", severity="CRITICAL", repair_status="UNREPAIRED",
                                      evidence_header=True), spec)
    assert "CC008" in _ids(F.card("ZZ-C01", authoring={"author_id": "a", "created_on": "2026-01-01",
                                                       "reviewers": ["a"]}), spec)
    assert "CC009" in _ids(F.card("ZZ-C01", authoring={"author_id": "a", "created_on": "2026-01-01",
                                                       "reviewers": ["b"], "llm_assisted": True}), spec)
    assert "CC010" in _ids(F.card("ZZ-C01", dangerous_win="CRITICAL"), spec)
    assert "CC011" in _ids(F.card("ZZ-C01", clean_loss=True, outcome={"dispositions": ["PAYMENT_CLAIMED_IN_CALL"],
                                                                       "positive": True}), spec)
    assert "CC012" in _ids(F.card("ZZ-C01", expected_evaluability="NOT_EVALUABLE"), spec)
    assert "CC013" in _ids(F.card("ZZ-C01", pack="redteam", split="holdout"), spec)
    assert "CC014" in _ids(F.card("ZZ-C01", target_check="ACC-01", severity="MAJOR", repair_status=None,
                                  evidence_turns=[1]), spec)
    assert _ids(F.card("ZZ-C01", target_check="ACC-01"), spec) == set()  # an always-OUT_OF_SCOPE expectation


def test_cc015_repair_only_on_acc05(spec):
    """AJ-08: the repair allowlist is {ACC-05}; no Minor -> Informational repair."""
    assert "CC015" in _ids(F.card("ZZ-C01", target_check="TRT-06", severity="INFORMATIONAL",
                                  repair_status="REPAIRED", evidence_turns=[2]), spec)
    assert "CC015" in _ids(F.card("ZZ-C01", target_check="UND-01", severity="MINOR", repair_status="REPAIRED",
                                  evidence_turns=[2]), spec)
    assert "CC015" not in _ids(F.card("ZZ-C01", target_check="ACC-05", severity="MINOR", repair_status="REPAIRED",
                                      evidence_turns=[2]), spec)
    assert "CC006" in _ids(F.card("ZZ-C01", target_check="ACC-05", severity="INFORMATIONAL",
                                  repair_status="REPAIRED", evidence_turns=[2]), spec)  # repaired ACC-05 is MINOR
    assert spec.registry.repair_allowlist == ("ACC-05",)

