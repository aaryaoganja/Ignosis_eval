"""Shared front end (L0–L3): normalization, role gate, evaluability and deterministic pre-checks."""

from __future__ import annotations

import json

import pytest

import factories as F
from ignosis_eval.contracts.benchmark import ItemMeta
from ignosis_eval.contracts.canonical_input import TranscriptHeader
from ignosis_eval.contracts.enums import InputMode, UnitMode
from ignosis_eval.pipeline.asr import ASRUnavailableError, ReplayASR
from ignosis_eval.pipeline.intake import parse_txt
from ignosis_eval.pipeline.normalize import NormalizationError, build_normalized_input, unit_facts
from ignosis_eval.pipeline.prechecks import precheck_g7, run_frontend


def _item(tmp_path, text, modes=("TRANSCRIPT",), audio=False, platform=None):
    d = tmp_path / "item"
    d.mkdir()
    (d / "t.txt").write_text(text)
    arts = {"transcript": "t.txt"}
    if audio:
        (d / "a.wav").write_bytes(b"RIFF-stub")
        arts["audio"] = "a.wav"
    if platform is not None:
        (d / "p.txt").write_text(platform)
        arts["platform_transcript"] = "p.txt"
    meta = ItemMeta.model_validate({"item_id": "ZZ-F01", "split": "dev", "pack": "core", "language": "en",
                                    "unit_modes": list(modes), "artifacts": arts})
    return meta, d


def test_transcript_unit(tmp_path, spec):
    meta, d = _item(tmp_path, F.stub_transcript())
    ni = build_normalized_input(meta, d, UnitMode.TRANSCRIPT, spec)
    assert [t.turn for t in ni.turns] == [1, 2, 3, 4, 5] and ni.frontend.evaluability_status.value == "EVALUABLE"
    pre = {g.gate.value: g.status.value for g in ni.frontend.prechecks}
    assert pre == {"G7": "OUT_OF_SCOPE", "G8": "NA", "G9": "NA"}  # default profile: G8/G9 lists empty
    statuses = {s.check: s.status for s in ni.frontend.steps}
    assert statuses["DC-02"] == "pending_signoff" and statuses["DC-LANG"] == "pending_signoff"
    assert statuses["EVALUABILITY-PARTIAL"] == "not_applicable"  # derived by the verdict engine (V7, AJ-04)


def test_unlabeled_transcript_not_evaluable(tmp_path, spec):
    meta, d = _item(tmp_path, "stub line one\nstub line two\n")
    ni = build_normalized_input(meta, d, UnitMode.TRANSCRIPT, spec)
    assert ni.frontend.evaluability_status.value == "NOT_EVALUABLE"
    assert [r.value for r in ni.frontend.reason_codes] == ["ROLE_UNCERTAIN"]


@pytest.mark.parametrize("ts,expected", [
    ("2026-09-28T08:00:00+05:30", "PASS"),        # window start is inside
    ("2026-09-28T18:59:59+05:30", "PASS"),
    ("2026-09-28T19:00:00+05:30", "FAIL"),        # half-open window [08:00, 19:00)
    ("2026-09-28T07:59:00+05:30", "FAIL"),
    ("2026-09-28T02:00:00+00:00", "FAIL"),        # 07:30 IST
    ("2026-09-28T03:00:00+00:00", "PASS"),        # 08:30 IST
])
def test_g7_calling_window(spec, ts, expected):
    h = TranscriptHeader(call_start_ts=ts)
    g = precheck_g7(h, InputMode.TRANSCRIPT, spec)
    assert g.status.value == expected
    if expected == "FAIL":
        assert g.critical_status.value == "CONFIRMED" and g.confidence.value == "HIGH"
        assert g.attribution.primary.value == "INDETERMINATE"


def test_g7_oos_rules(spec):
    g = precheck_g7(TranscriptHeader(), InputMode.TRANSCRIPT, spec)
    assert (g.status.value, g.oos_reason.value) == ("OUT_OF_SCOPE", "EXTERNAL_DATA_REQUIRED")
    g = precheck_g7(TranscriptHeader(call_start_ts="2026-09-28T02:00:00+00:00"), InputMode.AUDIO, spec)
    assert (g.status.value, g.oos_reason.value) == ("OUT_OF_SCOPE", "MODE_CAPABILITY")  # R-08


def test_truncation_header_reason(spec):
    p = parse_txt("# truncated_start: true\nAGENT: stub\nBORROWER: stub\n")
    from ignosis_eval.contracts.canonical_input import Turn

    turns = [Turn(turn=i, role=t.role, text=t.text) for i, t in enumerate(p.turns, 1)]
    fr = run_frontend(turns, p.header, InputMode.TRANSCRIPT, spec)
    assert "TRANSCRIPT_TRUNCATED" in [r.value for r in fr.reason_codes]


def test_audio_units_need_cached_asr(tmp_path, spec):
    meta, d = _item(tmp_path, F.stub_transcript(header={"call_start_ts": "2026-09-28T10:00:00+05:30"}),
                    modes=("T-gold", "T-asr", "A", "A+T"), audio=True)
    with pytest.raises(ASRUnavailableError):
        build_normalized_input(meta, d, UnitMode.A, spec)
    asr = ReplayASR(engine="stub", version="0", cache_dir=tmp_path / "cache")
    ni_tgold = build_normalized_input(meta, d, UnitMode.T_GOLD, spec)
    import hashlib

    sha = hashlib.sha256((d / "a.wav").read_bytes()).hexdigest()
    ReplayASR.write_cache_entry(tmp_path / "cache", asr, sha, [
        {"role": "AGENT", "text": "stub asr alpha", "start_s": 0.0, "end_s": 1.0, "diarization_confidence": 0.99},
        {"role": "BORROWER", "text": "stub asr beta", "start_s": 1.2, "end_s": 2.0, "diarization_confidence": 0.99}],
        mapping_confidence=0.97)
    ni_a = build_normalized_input(meta, d, UnitMode.A, spec, asr)
    assert ni_a.header.call_start_ts is None  # R-08: never from audio
    assert {g.gate.value: g.status.value for g in ni_a.frontend.prechecks}["G7"] == "OUT_OF_SCOPE"
    ni_tasr = build_normalized_input(meta, d, UnitMode.T_ASR, spec, asr)
    assert ni_tasr.header.transcript_provenance.value == "offline_asr" and ni_tasr.audio is None
    assert ni_tgold.input_mode is InputMode.TRANSCRIPT
    facts = {f.unit_mode.value: f for f in unit_facts(meta, d)}
    assert facts["A"].has_call_start_ts is False and facts["T-gold"].has_call_start_ts is True
    assert facts["T-asr"].provenance.value == "offline_asr"


def test_platform_transcript_requires_declared_provenance(tmp_path, spec):
    meta, d = _item(tmp_path, F.stub_transcript(), modes=("A+T-platform",), audio=True,
                    platform=F.stub_transcript(header={"transcript_provenance": "human"}))
    with pytest.raises(NormalizationError):
        build_normalized_input(meta, d, UnitMode.A_T_PLATFORM, spec)
    assert json.loads(json.dumps(meta.model_dump(mode="json")))["unit_modes"] == ["A+T-platform"]


# ------------------------------------------------------------------ AJ-05: call-level role gate, UNKNOWN turns
def _turns(*roles):
    from ignosis_eval.contracts.canonical_input import Turn
    from ignosis_eval.contracts.enums import Role

    return [Turn(turn=i, role=Role(r), text=f"stub {r.lower()} line {i}", unreliable=r == "UNKNOWN")
            for i, r in enumerate(roles, 1)]


@pytest.mark.parametrize("mapping,expected", [(1.0, "EVALUABLE"), (0.85, "EVALUABLE"), (0.8499, "NOT_EVALUABLE"),
                                              (0.2, "NOT_EVALUABLE")])
def test_dc00_call_level_mapping_confidence(spec, mapping, expected):
    assert spec.threshold("role_confidence_min") == 0.85
    fr = run_frontend(_turns("AGENT", "BORROWER", "AGENT"), TranscriptHeader(), InputMode.TRANSCRIPT, spec,
                      role_mapping_confidence=mapping)
    assert fr.evaluability_status.value == expected
    if expected == "NOT_EVALUABLE":
        assert [r.value for r in fr.reason_codes] == ["ROLE_UNCERTAIN"]


@pytest.mark.parametrize("roles,reason", [
    (("BORROWER", "BORROWER"), "ROLE_UNCERTAIN"),              # DC-00: no AGENT turn
    (("UNKNOWN", "UNKNOWN"), "ROLE_UNCERTAIN"),                # DC-00 comes first (SC-03 order)
    (("AGENT", "AGENT"), "NON_CONVERSATIONAL"),                # SC-03: DC-02 before the no-BORROWER clause
    (("AGENT", "UNKNOWN", "OTHER"), "NON_CONVERSATIONAL"),     # OTHER / UNKNOWN turns are not BORROWER turns
])
def test_sc03_evaluability_order(spec, roles, reason):
    """rubric.yaml › evaluability_order (SC-03): DC-00 mapping/no-AGENT, then DC-02, then the no-BORROWER clause
    only if DC-02 did not apply. A call with no BORROWER turn meets DC-02 ("no borrower turn with >= N words")
    whatever the pending threshold, so it is NON_CONVERSATIONAL, never ROLE_UNCERTAIN."""
    fr = run_frontend(_turns(*roles), TranscriptHeader(), InputMode.TRANSCRIPT, spec)
    assert fr.evaluability_status.value == "NOT_EVALUABLE" and [r.value for r in fr.reason_codes] == [reason]
    order = spec.rubric["evaluability_order"]
    assert [i for i, o in enumerate(order) if "DC-02" in o] < [i for i, o in enumerate(order) if "no-BORROWER" in o]
    steps = {s.check: s.status for s in fr.steps}
    assert steps["DC-02"] == ("not_applicable" if reason == "ROLE_UNCERTAIN" else "done")


def test_non_conversational_call_still_runs_prechecks(spec):
    fr = run_frontend(_turns("AGENT", "AGENT"), TranscriptHeader(call_start_ts="2026-09-28T21:00:00+05:30"),
                      InputMode.TRANSCRIPT, spec)
    assert [r.value for r in fr.reason_codes] == ["NON_CONVERSATIONAL"]
    assert {p.gate.value: p.status.value for p in fr.prechecks}["G7"] == "FAIL"


def test_unknown_turns_are_span_unreliable_not_call_level(tmp_path, spec):
    text = "AGENT: stub agent line one\nBORROWER: stub borrower line two\nUNKNOWN: stub unknown line three\n" \
           "AGENT: stub agent line four\n"
    meta, d = _item(tmp_path, text)
    ni = build_normalized_input(meta, d, UnitMode.TRANSCRIPT, spec)
    assert ni.frontend.evaluability_status.value == "EVALUABLE"  # an UNKNOWN turn does not make the call NE
    assert ni.role_mapping_confidence == 1.0  # transcript role labels
    assert [t.unreliable for t in ni.turns] == [False, False, True, False]
    steps = {s.check: s.status for s in ni.frontend.steps}
    assert steps["DC-01-unknown-role-turns"] == "done" and "DC-01-diarization-turn" not in steps


def test_unknown_turn_evidence_is_low_confidence(spec):
    from ignosis_eval.contracts.evaluation_record import RecordBody, SystemInfo
    from ignosis_eval.engine.finalize import Facts, finalize

    ni = F.make_ni(spec, (("AGENT", "stub agent line alpha"), ("BORROWER", "stub borrower line beta"),
                          ("UNKNOWN", "stub unknown line gamma")))
    assert ni.turns[2].unreliable
    body = RecordBody(gates=[F.gate("G4", "FAIL", evidence=[F.ev(3, "stub unknown line gamma", "UNKNOWN")])])
    rec = finalize(body, ni, spec, system=SystemInfo(system="B", version="t"), facts=Facts(det_confirmed={"G4": True}))
    g4 = rec.gate("G4")
    assert g4.confidence.value == "LOW" and g4.critical_status.value == "SUSPECTED"  # never CONFIRMED on an UNKNOWN span


def test_diarized_turn_threshold_is_pending(spec):
    fr = run_frontend(_turns("AGENT", "BORROWER"), TranscriptHeader(), InputMode.AUDIO, spec,
                      role_mapping_confidence=0.97, diarized=True)
    step = {s.check: s for s in fr.steps}["DC-01-diarization-turn"]
    assert step.status == "pending_signoff" and step.blocker == "B-06/B-11"
    with pytest.raises(Exception, match="PENDING"):
        spec.threshold("diarization_turn_min_confidence")


def test_asr_cache_requires_mapping_confidence(tmp_path, spec):
    import hashlib

    meta, d = _item(tmp_path, F.stub_transcript(), modes=("A",), audio=True)
    asr = ReplayASR(engine="stub", version="0", cache_dir=tmp_path / "cache")
    sha = hashlib.sha256((d / "a.wav").read_bytes()).hexdigest()
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / asr.cache_key(sha)).write_text(json.dumps({"turns": [
        {"role": "AGENT", "text": "stub asr alpha", "start_s": 0.0, "end_s": 1.0}]}))
    with pytest.raises(ASRUnavailableError, match="mapping_confidence"):
        build_normalized_input(meta, d, UnitMode.A, spec, asr)
    ReplayASR.write_cache_entry(tmp_path / "cache", asr, sha, [
        {"role": "AGENT", "text": "stub asr alpha", "start_s": 0.0, "end_s": 1.0, "diarization_confidence": 0.5},
        {"role": "BORROWER", "text": "stub asr beta", "start_s": 1.2, "end_s": 2.0}], mapping_confidence=0.6)
    ni = build_normalized_input(meta, d, UnitMode.A, spec, asr)
    assert ni.role_mapping_confidence == 0.6 and ni.frontend.evaluability_status.value == "NOT_EVALUABLE"
    assert ni.turns[0].diarization_confidence == 0.5
