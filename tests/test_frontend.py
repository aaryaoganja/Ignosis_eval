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
    assert statuses["EVALUABILITY-PARTIAL"] == "not_implemented"


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

    turns = [Turn(turn=i, role=t.role, text=t.text, role_confidence=1.0) for i, t in enumerate(p.turns, 1)]
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
        {"role": "AGENT", "text": "stub asr alpha", "start_s": 0.0, "end_s": 1.0, "role_confidence": 0.99},
        {"role": "BORROWER", "text": "stub asr beta", "start_s": 1.2, "end_s": 2.0, "role_confidence": 0.99}])
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
