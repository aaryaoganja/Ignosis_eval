"""Schema validation and contract invariants (Canonical Input, Evaluation Record, Gold Label)."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from factories import (
    CALL_TS,
    PROFILE,
    ev,
    expected_defect,
    finding,
    gate,
    make_audio,
    make_gold,
    make_input,
    make_record,
    make_transcript,
    make_turns,
)
from ignosis_eval.canonical import canonical_sha256
from ignosis_eval.contracts import (
    AudioMetadata,
    CanonicalInput,
    DimensionResult,
    EvaluationRecord,
    EvidenceItem,
    ExperimentMetadata,
    Turn,
)
from ignosis_eval.contracts.enums import (
    AttributionTarget,
    ContestedStatus,
    EvaluabilityStatus,
    EvidenceModality,
    GateStatus,
    InputMode,
    LabelConfidence,
    Severity,
    Speaker,
    Split,
    TranscriptSource,
    Verdict,
)
from ignosis_eval.contracts.evidence import check_evidence, normalize_text
from ignosis_eval.contracts.gold_label import Contested, ExpectedDefect, GoldLabel
from ignosis_eval.contracts.record_checks import (
    IntegrityCode,
    ModalityCode,
    check_modality_conformance,
    check_record_integrity,
)


# ---------------------------------------------------------------------------------------------- input
@pytest.mark.parametrize("mode", list(InputMode))
def test_canonical_input_valid_in_all_three_modes(mode):
    src = TranscriptSource.PIPELINE_ASR if mode is InputMode.AUDIO_ONLY else None
    inp = make_input(mode=mode, transcript_source=src)
    assert inp.input_mode is mode
    # JSON round trip is lossless
    assert CanonicalInput.model_validate(inp.to_json_dict()) == inp


def test_audio_only_without_transcript_is_valid():
    inp = make_input(mode=InputMode.AUDIO_ONLY)
    assert inp.transcript is None and inp.audio is not None


def _raw_input(**over):
    base = make_input(mode=InputMode.AUDIO_TRANSCRIPT).to_json_dict()
    base.update(over)
    return base


def test_transcript_only_rejects_audio():
    d = _raw_input(input_mode="transcript_only")
    with pytest.raises(ValidationError, match="must not carry audio"):
        CanonicalInput.model_validate(d)


def test_audio_only_rejects_non_pipeline_transcript():
    d = _raw_input(input_mode="audio_only")
    with pytest.raises(ValidationError, match="pipeline's own ASR"):
        CanonicalInput.model_validate(d)


def test_audio_transcript_requires_both():
    d = _raw_input(audio=None)
    d["evidence_availability"]["audio"] = False
    with pytest.raises(ValidationError, match="requires both"):
        CanonicalInput.model_validate(d)


def test_evidence_availability_must_match_payload():
    d = _raw_input()
    d["evidence_availability"]["call_start_ts"] = False
    with pytest.raises(ValidationError, match="call_start_ts disagrees"):
        CanonicalInput.model_validate(d)
    d = _raw_input()
    d["transcript"]["turns"][0]["start_ms"] = None
    d["transcript"]["turns"][0]["end_ms"] = None
    with pytest.raises(ValidationError, match="turn_timestamps"):
        CanonicalInput.model_validate(d)


def test_call_start_ts_must_be_timezone_aware():
    d = _raw_input(call_start_ts=datetime(2026, 9, 1, 10, 30).isoformat())
    with pytest.raises(ValidationError):
        CanonicalInput.model_validate(d)


def test_turn_invariants():
    with pytest.raises(ValidationError, match="start_ms > end_ms"):
        Turn(turn_id="t1", index=0, speaker=Speaker.AGENT, text="x", start_ms=10, end_ms=5)
    turns = make_turns()
    dup = [turns[0], turns[1].model_copy(update={"turn_id": "t01"})]
    with pytest.raises(ValidationError, match="duplicate turn_id"):
        make_transcript(dup)
    with pytest.raises(ValidationError, match="index"):
        make_transcript([turns[1]])
    swapped = [turns[0].model_copy(update={"start_ms": 9000, "end_ms": 9500}), turns[1]]
    with pytest.raises(ValidationError, match="decreases"):
        make_transcript(swapped)


def test_unknown_fields_rejected():
    d = _raw_input(crm_account_balance=1234)
    with pytest.raises(ValidationError, match="Extra inputs"):
        CanonicalInput.model_validate(d)


def test_schema_version_is_exact():
    d = _raw_input(schema_version="canonical_input/2.0.0")
    with pytest.raises(ValidationError):
        CanonicalInput.model_validate(d)


def test_audio_uri_must_be_relative():
    for bad in ("/etc/passwd", "../other-case/audio.wav"):
        with pytest.raises(ValidationError, match="relative"):
            AudioMetadata.model_validate({**make_audio().to_json_dict(), "uri": bad})


# --------------------------------------------------------------------------------------------- record
def test_record_structural_validation():
    with pytest.raises(ValidationError, match="requires turn_ids"):
        EvidenceItem(evidence_id="e1", modality=EvidenceModality.TRANSCRIPT)
    with pytest.raises(ValidationError, match="requires start_ms"):
        EvidenceItem(evidence_id="e1", modality=EvidenceModality.AUDIO)
    with pytest.raises(ValidationError, match="requires a score"):
        DimensionResult(dimension_id="D_CLARITY", evaluable=True)
    with pytest.raises(ValidationError, match="must not carry a score"):
        DimensionResult(dimension_id="D_CLARITY", evaluable=False, score=3)
    with pytest.raises(ValidationError, match="duplicate evidence_ids"):
        make_record(evidence=[ev("e1", ["t01"]), ev("e1", ["t02"])])


def test_record_round_trip_and_content_hash_ignores_experiment():
    rec = make_record(evidence=[ev("e1", ["t01"], "on behalf of Acme")])
    assert EvaluationRecord.model_validate(rec.to_json_dict()) == rec
    with_exp = rec.model_copy(update={"experiment": ExperimentMetadata(
        run_id="r", item_id="case-0001", repetition=1, seed=1, rep_seed=2, started_at=CALL_TS, finished_at=CALL_TS)})
    assert with_exp.content_hash() == rec.content_hash()
    assert rec.model_copy(update={"verdict": Verdict.FAIL}).content_hash() != rec.content_hash()


def test_gate_precedence_violation_detected():
    rec = make_record(verdict=Verdict.PASS, gates=[gate("G_NO_THREATS_OR_ABUSE", GateStatus.FAIL)])
    codes = {v.code for v in check_record_integrity(rec)}
    assert IntegrityCode.GATE_PRECEDENCE_VIOLATION in codes
    ok = make_record(verdict=Verdict.FAIL, gates=[gate("G_NO_THREATS_OR_ABUSE", GateStatus.FAIL)])
    assert IntegrityCode.GATE_PRECEDENCE_VIOLATION not in {v.code for v in check_record_integrity(ok)}


@pytest.mark.parametrize("verdict,status", [
    (Verdict.PASS, EvaluabilityStatus.INCONCLUSIVE),
    (Verdict.OUT_OF_SCOPE, EvaluabilityStatus.EVALUABLE),
    (Verdict.INCONCLUSIVE, EvaluabilityStatus.OUT_OF_SCOPE),
])
def test_verdict_evaluability_mismatch(verdict, status):
    rec = make_record(verdict=verdict, evaluability=status)
    assert IntegrityCode.VERDICT_EVALUABILITY_MISMATCH in {v.code for v in check_record_integrity(rec)}


@pytest.mark.parametrize("verdict", [Verdict.OUT_OF_SCOPE, Verdict.INCONCLUSIVE])
def test_abstention_records_are_valid(verdict):
    rec = make_record(verdict=verdict, dangerous_win=None, clean_loss=None)
    assert check_record_integrity(rec, make_input(), PROFILE) == []


def test_dangling_refs_unknown_turns_and_ids():
    inp = make_input()
    rec = make_record(
        verdict=Verdict.FAIL,
        gates=[gate("G_NO_THREATS_OR_ABUSE", GateStatus.FAIL, ["e9"]), gate("G_NOT_IN_PROFILE", GateStatus.PASS)],
        findings=[finding("f1", "DEF_THREAT_OR_INTIMIDATION", evidence_ids=[]), finding("f2", "DEF_MADE_UP")],
        evidence=[ev("e1", ["t99"])],
        dangerous_win=True, clean_loss=True,
    )
    codes = [v.code for v in check_record_integrity(rec, inp, PROFILE)]
    for c in (IntegrityCode.DANGLING_EVIDENCE_REF, IntegrityCode.UNKNOWN_TURN_REF, IntegrityCode.FINDING_WITHOUT_EVIDENCE,
              IntegrityCode.UNKNOWN_GATE_ID, IntegrityCode.UNKNOWN_DEFECT_ID, IntegrityCode.DW_CL_CONTRADICTION):
        assert c in codes, c


def test_call_id_and_mode_mismatch():
    rec = make_record(call_id="other", input_mode=InputMode.AUDIO_TRANSCRIPT)
    codes = {v.code for v in check_record_integrity(rec, make_input())}
    assert {IntegrityCode.CALL_ID_MISMATCH, IntegrityCode.INPUT_MODE_MISMATCH} <= codes


def test_modality_conformance_violations():
    inp = make_input(call_start_ts=None)  # transcript only, no call_start_ts
    rec = make_record(
        verdict=Verdict.FAIL,
        gates=[gate("G_CONTACT_HOURS", GateStatus.FAIL, ["e2"])],
        findings=[finding("f1", "DEF_AGGRESSIVE_TONE", Severity.MAJOR, evidence_ids=["e1"])],
        evidence=[ev("e1", [], modality=EvidenceModality.AUDIO, start_ms=0, end_ms=100),
                  ev("e2", [], modality=EvidenceModality.METADATA, metadata_field="call_start_ts")],
    )
    codes = {v.code for v in check_modality_conformance(rec, inp, PROFILE)}
    assert codes == {
        ModalityCode.AUDIO_EVIDENCE_WITHOUT_AUDIO,
        ModalityCode.METADATA_EVIDENCE_UNAVAILABLE,
        ModalityCode.GATE_ASSERTED_WITHOUT_CAPABILITY,
        ModalityCode.DEFECT_ASSERTED_WITHOUT_CAPABILITY,
    }
    # INCONCLUSIVE on an unsupported gate is the conformant behaviour
    ok = make_record(verdict=Verdict.PASS, gates=[gate("G_CONTACT_HOURS", GateStatus.INCONCLUSIVE)])
    assert check_modality_conformance(ok, inp, PROFILE) == []


# ------------------------------------------------------------------------------------------- evidence
def test_evidence_faithfulness_checks():
    inp = make_input(mode=InputMode.AUDIO_TRANSCRIPT)
    assert check_evidence(ev("e", ["t01"], "ON BEHALF OF  acme finance"), inp).faithful
    assert not check_evidence(ev("e", ["t01"], "on behalf of Globex"), inp).faithful
    assert not check_evidence(ev("e", ["t42"], None), inp).faithful
    assert not check_evidence(ev("e", ["t01"], None, speaker=Speaker.CUSTOMER), inp).faithful
    assert check_evidence(ev("e", ["t01"], None, start_ms=100, end_ms=3000), inp).faithful
    assert not check_evidence(ev("e", ["t01"], None, start_ms=100, end_ms=30_000), inp).faithful
    assert check_evidence(ev("e", [], modality=EvidenceModality.AUDIO, start_ms=0, end_ms=59_000), inp).faithful
    assert not check_evidence(ev("e", [], modality=EvidenceModality.AUDIO, start_ms=0, end_ms=99_000), inp).faithful
    transcript_only = make_input()
    assert not check_evidence(ev("e", [], modality=EvidenceModality.AUDIO, start_ms=0, end_ms=10), transcript_only).faithful


def test_normalize_text_handles_devanagari_and_punctuation():
    assert normalize_text("आप  कल भुगतान करेंगे।") == "आप कल भुगतान करेंगे"
    assert normalize_text("Rs. 4,500!") == "rs 4 500"


# ----------------------------------------------------------------------------------------------- gold
def test_gold_valid_and_round_trip():
    g = make_gold(verdict=Verdict.FAIL, gates=[("G_NO_THREATS_OR_ABUSE", GateStatus.FAIL)],
                  defects=[expected_defect("DEF_THREAT_OR_INTIMIDATION", gate_id="G_NO_THREATS_OR_ABUSE")],
                  dangerous_win=True)
    assert GoldLabel.model_validate(g.to_json_dict()) == g


def test_gold_cannot_be_derived_from_evaluator_output():
    d = make_gold().to_json_dict()
    d["provenance"]["derived_from_evaluator_output"] = True
    with pytest.raises(ValidationError):
        GoldLabel.model_validate(d)
    d = make_gold().to_json_dict()
    d["provenance"]["source_run_id"] = "20260901-run"
    with pytest.raises(ValidationError, match="Extra inputs"):
        GoldLabel.model_validate(d)


def test_holdout_gold_must_be_blind_to_evaluator_outputs():
    make_gold(split=Split.DEV, saw_outputs=True)  # allowed (recorded) on dev
    with pytest.raises(ValidationError, match="blind to evaluator outputs"):
        make_gold(split=Split.HOLDOUT, saw_outputs=True)


def test_gold_gate_precedence_and_evaluability():
    with pytest.raises(ValidationError, match="gate precedence"):
        make_gold(verdict=Verdict.PASS, gates=[("G_NO_THREATS_OR_ABUSE", GateStatus.FAIL)])
    d = make_gold().to_json_dict()
    d["expected_evaluability"]["status"] = "out_of_scope"
    with pytest.raises(ValidationError, match="inconsistent"):
        GoldLabel.model_validate(d)


def test_gold_misc_invariants():
    with pytest.raises(ValidationError, match="both be true"):
        make_gold(dangerous_win=True, clean_loss=True)
    with pytest.raises(ValidationError, match="attribution_determinable"):
        ExpectedDefect(defect_id="D", severity=Severity.MAJOR, attribution=AttributionTarget.UNDETERMINED,
                       attribution_determinable=True)
    with pytest.raises(ValidationError, match="duplicate defect_id"):
        make_gold(verdict=Verdict.FAIL, defects=[expected_defect("DEF_X"), expected_defect("DEF_X")])
    g = make_gold()
    d = g.to_json_dict()
    d["contested"] = Contested(status=ContestedStatus.CONTESTED_UNRESOLVED).to_json_dict()
    with pytest.raises(ValidationError, match="HIGH confidence"):
        GoldLabel.model_validate(d)
    d["confidence"] = LabelConfidence.LOW.value
    GoldLabel.model_validate(d)


def test_canonical_hash_is_key_order_and_unicode_invariant():
    a = {"b": 1, "a": "café"}
    b = {"a": "café", "b": 1}  # decomposed e + combining acute
    assert canonical_sha256(a) == canonical_sha256(b)
    assert canonical_sha256(a) != canonical_sha256({"a": "cafe", "b": 1})
    with pytest.raises(ValueError):
        canonical_sha256({"x": float("nan")})
