"""Independent gold derivation (P-12, SD-01): capability table, implicit gold, attribution, verdict recompute."""

from __future__ import annotations

import itertools

import pytest

import factories as F
from ignosis_eval.contracts.benchmark import UnitFacts
from ignosis_eval.contracts.enums import InputMode, TranscriptProvenance, UnitMode
from ignosis_eval.contracts.gold_label import AttributionFacts
from ignosis_eval.golddrv.capability import CapabilityTable
from ignosis_eval.golddrv.derive import GoldDerivationError, derive_attribution, derive_mode_gold

MODE_INPUT = {"TRANSCRIPT": "TRANSCRIPT", "T-gold": "TRANSCRIPT", "T-asr": "TRANSCRIPT", "A": "AUDIO",
              "A+T": "AUDIO_TRANSCRIPT", "A+T-platform": "AUDIO_TRANSCRIPT"}


def facts(mode="TRANSCRIPT", *, header=True, ts=False, prov=None, item="ZZ-G01"):
    return UnitFacts(item_id=item, unit_mode=mode, input_mode=MODE_INPUT[mode], has_call_start_ts=header,
                     has_timestamps=ts, provenance=prov, truncated_start=False)


def test_capability_resolver_agrees_with_engine_registry(spec):
    """Two independent implementations of §8 (evaluator engine vs gold derivation) must agree everywhere."""
    table = CapabilityTable.from_rubric(spec.rubric)
    reg = spec.registry
    provs = [None, *TranscriptProvenance]
    for cid, mode, hdr, ts, prov in itertools.product(reg.checks, InputMode, (True, False), (True, False), provs):
        eng = reg.mode_status(cid, mode, has_call_start_ts=hdr, has_timestamps=ts, provenance=prov)
        ok, reason = table.in_scope(cid, mode.value, has_call_start_ts=hdr, has_timestamps=ts,
                                    provenance=prov.value if prov else None)
        assert (eng[0].value == "EVALUABLE") == ok, (cid, mode, hdr, ts, prov)
        if not ok:
            assert eng[1].value == reason


def test_section8_rows(spec):
    t = CapabilityTable.from_rubric(spec.rubric)
    assert t.in_scope("G7", "AUDIO", has_call_start_ts=True, has_timestamps=True, provenance=None)[0] is False
    assert t.in_scope("G7", "TRANSCRIPT", has_call_start_ts=False, has_timestamps=False, provenance=None) == \
        (False, "EXTERNAL_DATA_REQUIRED")
    assert t.in_scope("PLT-01", "TRANSCRIPT", has_call_start_ts=False, has_timestamps=True, provenance=None)[0]
    assert not t.in_scope("PLT-02", "TRANSCRIPT", has_call_start_ts=True, has_timestamps=True, provenance=None)[0]
    assert t.in_scope("PLT-03", "AUDIO_TRANSCRIPT", has_call_start_ts=True, has_timestamps=True,
                      provenance="platform_live_asr")[0]
    assert not t.in_scope("PLT-04", "AUDIO_TRANSCRIPT", has_call_start_ts=True, has_timestamps=True,
                          provenance="human")[0]


def test_implicit_gold_and_explicit_lists(spec):
    g = F.gold("ZZ-G01", findings=[{"code": "UND-01", "anchor_turns": [2], "severity": "MAJOR"}],
               inconclusive_checks=["RES-05"], na_checks=["COM-01"], verdict="NEEDS_ATTENTION")
    mg = derive_mode_gold(g, facts(), spec.rubric)
    assert mg.codes["UND-01"].status == "DEFECT" and mg.codes["RES-05"].status == "INCONCLUSIVE"
    assert mg.codes["COM-01"].status == "NA" and mg.codes["UND-02"].status == "PASS"
    assert mg.codes["UND-02"].label_confidence == "Sure"
    assert mg.codes["PLT-02"].status == "OUT_OF_SCOPE"  # transcript mode
    assert not mg.requires_human_spot_check


def test_audio_mode_removes_g7_and_recomputes_verdict(spec):
    g = F.gold("ZZ-G01", gates={"G7": {"status": "FAIL"}}, findings=[
        {"code": "TRT-01", "anchor_turns": [3], "severity": "MAJOR"}], tags={"dangerous_win": "CRITICAL"},
        outcome={"dispositions": ["PTP_STATED"], "positive": True})
    tr = derive_mode_gold(g, facts("T-gold"), spec.rubric)
    assert tr.gates["G7"].status == "FAIL" and tr.verdict == "CRITICAL_FAIL"
    a = derive_mode_gold(g, facts("A", header=False, ts=True), spec.rubric)
    assert a.gates["G7"].status == "OUT_OF_SCOPE" and a.verdict == "NEEDS_ATTENTION"
    assert a.dangerous_win is None and a.requires_human_spot_check and a.notes


@pytest.mark.parametrize("cls,mode,prov,f,expected", [
    ("agent_speech", "TRANSCRIPT", None, {}, "AGENT_BEHAVIOR"),
    ("non_response", "TRANSCRIPT", None, {"registered": True}, "AGENT_BEHAVIOR"),
    ("non_response", "TRANSCRIPT", None, {}, "INDETERMINATE"),
    ("content_perception", "AUDIO_TRANSCRIPT", "human", {"said_heard_material_difference": True}, "INDETERMINATE"),
    ("content_perception", "AUDIO_TRANSCRIPT", "platform_live_asr", {"said_heard_material_difference": True},
     "PERCEPTION"),
    ("content_perception", "AUDIO_TRANSCRIPT", "platform_live_asr", {"said_heard_material_difference": False},
     "AGENT_BEHAVIOR"),
    ("content_perception", "AUDIO_TRANSCRIPT", "platform_live_asr", {}, None),  # undetermined, never guessed
    ("timing", "AUDIO", None, {}, "PLATFORM_AUDIO"),
    ("perception_event", "AUDIO_TRANSCRIPT", "platform_live_asr", {}, "PERCEPTION"),
    ("not_attributable", "TRANSCRIPT", None, {}, "INDETERMINATE"),
])
def test_attribution_derivation(cls, mode, prov, f, expected):
    assert derive_attribution(cls, AttributionFacts(**f), mode, prov)[0] == expected


def test_abstention_targets_remapped_and_errors(spec):
    g = F.gold("ZZ-G01", gates={"G7": {"status": "INCONCLUSIVE", "trigger": False}},
               abstention_targets=[{"check": "G7", "expected": "INCONCLUSIVE"}], wsc=False)
    a = derive_mode_gold(g, facts("A", header=False, ts=True), spec.rubric)
    assert a.abstention_targets == (("G7", "OUT_OF_SCOPE"),) and a.within_scope_complete is True
    with pytest.raises(GoldDerivationError):
        derive_mode_gold(F.gold("ZZ-G01", findings=[{"code": "ACC-01", "anchor_turns": [1], "severity": "MAJOR"}]),
                         facts(), spec.rubric)
    with pytest.raises(GoldDerivationError):
        derive_mode_gold(F.gold("ZZ-G02"), facts(), spec.rubric)  # wrong item
    assert UnitMode.A.value == "A"
