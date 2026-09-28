"""Hand-built contract objects for tests. Nothing here is benchmark data or gold."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ignosis_eval.contracts import (
    AttributionClaim,
    AudioMetadata,
    CanonicalInput,
    EvaluabilityAssessment,
    EvaluationRecord,
    EvaluatorInfo,
    EvidenceItem,
    Finding,
    GateResult,
    LanguageInfo,
    ObservableOutcomes,
    Routing,
    Transcript,
    TranscriptProvenance,
    Turn,
    derive_evidence_availability,
    load_profile,
)
from ignosis_eval.contracts.enums import (
    AttributionTarget,
    EvaluabilityStatus,
    EvaluatorArchitecture,
    EvidenceModality,
    GateStatus,
    GoldSource,
    InputMode,
    LabelConfidence,
    LabelerRole,
    OutcomeClass,
    RoutingDecision,
    Severity,
    Speaker,
    Split,
    TimestampSource,
    TranscriptSource,
    Verdict,
)
from ignosis_eval.contracts.gold_label import (
    ExpectedDefect,
    ExpectedEvaluability,
    ExpectedEvidence,
    ExpectedGate,
    GoldLabel,
    GoldProvenance,
    LabelerRecord,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = REPO_ROOT / "config" / "profiles" / "collections_placeholder.yaml"
PROFILE, PROFILE_SHA = load_profile(PROFILE_PATH)
IST = timezone(timedelta(hours=5, minutes=30))
CALL_TS = datetime(2026, 9, 1, 10, 30, tzinfo=IST)
LABEL_TS = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

DEFAULT_TURNS = [
    (Speaker.AGENT, "Hello, this is Priya calling on behalf of Acme Finance regarding your loan."),
    (Speaker.CUSTOMER, "Yes, speaking."),
    (Speaker.AGENT, "Your EMI of 4,500 rupees is overdue. Can you pay by Friday?"),
    (Speaker.CUSTOMER, "Okay, I will pay on Friday."),
]


def make_turns(spec=None, timed: bool = True) -> list[Turn]:
    spec = spec or DEFAULT_TURNS
    turns = []
    for i, (spk, text) in enumerate(spec):
        turns.append(
            Turn(
                turn_id=f"t{i + 1:02d}", index=i, speaker=spk, text=text,
                start_ms=i * 5000 if timed else None, end_ms=i * 5000 + 4000 if timed else None,
            )
        )
    return turns


def make_transcript(turns=None, source=TranscriptSource.SYNTHETIC_SCRIPT, audio_sha: str | None = None) -> Transcript:
    turns = turns if turns is not None else make_turns()
    kwargs = {}
    if source in (TranscriptSource.PIPELINE_ASR, TranscriptSource.VENDOR_ASR, TranscriptSource.HUMAN_CORRECTED_ASR):
        kwargs["asr_engine"] = "mock-asr"
    if source is TranscriptSource.PIPELINE_ASR:
        kwargs["derived_from_audio_sha256"] = audio_sha or "0" * 64
    return Transcript(
        provenance=TranscriptProvenance(
            source=source, diarization="scripted", timestamp_source=TimestampSource.SCRIPT_PLANNED, **kwargs
        ),
        turns=turns,
    )


def make_audio(duration_ms: int = 60_000) -> AudioMetadata:
    return AudioMetadata(
        uri="audio.wav", sha256="a" * 64, format="wav", sample_rate_hz=8000, channels=1,
        duration_ms=duration_ms, contains_real_pii=False,
    )


def make_input(
    call_id: str = "call-0001",
    mode: InputMode = InputMode.TRANSCRIPT_ONLY,
    turns=None,
    call_start_ts=CALL_TS,
    call_start_captured: bool = True,
    language: str = "en-IN",
    transcript_source: TranscriptSource | None = None,
) -> CanonicalInput:
    transcript = None
    audio = None
    if mode in (InputMode.TRANSCRIPT_ONLY, InputMode.AUDIO_TRANSCRIPT):
        transcript = make_transcript(turns, source=transcript_source or TranscriptSource.SYNTHETIC_SCRIPT)
    if mode in (InputMode.AUDIO_ONLY, InputMode.AUDIO_TRANSCRIPT):
        audio = make_audio()
    if mode is InputMode.AUDIO_ONLY and transcript_source is TranscriptSource.PIPELINE_ASR:
        transcript = make_transcript(turns, source=TranscriptSource.PIPELINE_ASR, audio_sha=audio.sha256)
    return CanonicalInput(
        call_id=call_id,
        input_mode=mode,
        language=LanguageInfo(primary=language),
        call_start_ts=call_start_ts,
        transcript=transcript,
        audio=audio,
        evidence_availability=derive_evidence_availability(
            transcript=transcript, audio=audio, call_start_ts_present=call_start_ts is not None,
            call_start_captured=call_start_captured, call_end_captured=True,
        ),
    )


def ev(evidence_id: str, turn_ids, quote: str | None = None, modality=EvidenceModality.TRANSCRIPT, **kw) -> EvidenceItem:
    return EvidenceItem(evidence_id=evidence_id, modality=modality, turn_ids=list(turn_ids), quote=quote, **kw)


def finding(
    finding_id: str, defect_id: str, severity=Severity.CRITICAL, gate_id: str | None = None,
    evidence_ids=("e1",), attribution=AttributionTarget.AGENT_LOGIC,
) -> Finding:
    return Finding(
        finding_id=finding_id, defect_id=defect_id, severity=severity, gate_id=gate_id,
        evidence_ids=list(evidence_ids), attribution=AttributionClaim(target=attribution),
    )


def gate(gate_id: str, status: GateStatus, evidence_ids=()) -> GateResult:
    return GateResult(gate_id=gate_id, status=status, evidence_ids=list(evidence_ids))


_EVAL_FOR_VERDICT = {
    Verdict.PASS: EvaluabilityStatus.EVALUABLE,
    Verdict.FAIL: EvaluabilityStatus.EVALUABLE,
    Verdict.INCONCLUSIVE: EvaluabilityStatus.INCONCLUSIVE,
    Verdict.OUT_OF_SCOPE: EvaluabilityStatus.OUT_OF_SCOPE,
}


def make_record(
    call_id: str = "call-0001",
    verdict: Verdict = Verdict.PASS,
    gates=(),
    findings=(),
    evidence=(),
    input_mode: InputMode = InputMode.TRANSCRIPT_ONLY,
    evaluability: EvaluabilityStatus | None = None,
    dangerous_win: bool | None = False,
    clean_loss: bool | None = False,
    outcome_class: OutcomeClass = OutcomeClass.WIN,
    record_id: str = "rec-1",
) -> EvaluationRecord:
    return EvaluationRecord(
        record_id=record_id,
        call_id=call_id,
        evaluator=EvaluatorInfo(name="handcrafted", version="0", architecture=EvaluatorArchitecture.MOCK),
        rubric_version=PROFILE.rubric_version,
        profile_id=PROFILE.profile_id,
        profile_version=PROFILE.profile_version,
        input_mode=input_mode,
        evaluability=EvaluabilityAssessment(status=evaluability or _EVAL_FOR_VERDICT[verdict]),
        verdict=verdict,
        gates=list(gates),
        findings=list(findings),
        evidence=list(evidence),
        observable_outcomes=ObservableOutcomes(outcome_class=outcome_class),
        dangerous_win=dangerous_win,
        clean_loss=clean_loss,
        routing=Routing(decision=RoutingDecision.AUTO_ACCEPT),
    )


def expected_defect(
    defect_id: str, severity=Severity.CRITICAL, gate_id: str | None = None, required_turns=("t03",),
    attribution=AttributionTarget.AGENT_LOGIC, required: bool = True, acceptable_turns=(),
) -> ExpectedDefect:
    return ExpectedDefect(
        defect_id=defect_id, severity=severity, gate_id=gate_id, required=required,
        evidence=ExpectedEvidence(required_turn_ids=list(required_turns), acceptable_turn_ids=list(acceptable_turns)),
        attribution=attribution, attribution_determinable=attribution is not AttributionTarget.UNDETERMINED,
    )


def make_gold(
    item_id: str = "case-0001",
    verdict: Verdict = Verdict.PASS,
    gates=(),
    defects=(),
    split: Split = Split.DEV,
    dangerous_win: bool | None = False,
    clean_loss: bool | None = False,
    acceptable_verdicts=(),
    acceptable_extra_defects=(),
    saw_outputs: bool = False,
    confidence: LabelConfidence = LabelConfidence.HIGH,
) -> GoldLabel:
    return GoldLabel(
        item_id=item_id,
        split=split,
        scenario="test_scenario",
        expected_evaluability=ExpectedEvaluability(status=_EVAL_FOR_VERDICT[verdict]),
        expected_verdict=verdict,
        acceptable_verdicts=list(acceptable_verdicts),
        expected_gates=[ExpectedGate(gate_id=g, status=s) for g, s in gates],
        expected_defects=list(defects),
        acceptable_extra_defects=list(acceptable_extra_defects),
        expected_dangerous_win=dangerous_win,
        expected_clean_loss=clean_loss,
        confidence=confidence,
        provenance=GoldProvenance(
            source=GoldSource.AUTHOR_SPECIFIED, labeling_protocol_version="test-protocol", created_at=LABEL_TS
        ),
        labelers=[
            LabelerRecord(
                labeler_id="lab-a", role=LabelerRole.AUTHOR, labeled_at=LABEL_TS,
                blind_to_case_card=False, saw_evaluator_outputs=saw_outputs,
            )
        ],
    )


# ------------------------------------------------------------------------------ scorer-test helpers
from ignosis_eval.contracts.benchmark import CapabilityBoundaries, CaseMetadata  # noqa: E402
from ignosis_eval.contracts.enums import SourceKind  # noqa: E402


def make_case(
    case_id: str = "case-0001", split: Split = Split.DEV, language: str = "en-IN",
    mode: InputMode = InputMode.TRANSCRIPT_ONLY, pair_id: str | None = None, pair_role: str | None = None,
    attribution_pair_id: str | None = None, judge_bait: bool = False,
) -> CaseMetadata:
    return CaseMetadata(
        case_id=case_id, split=split, scenario="test_scenario", category="test", intended_modality=mode,
        language=language, synthetic=True, source_kind=SourceKind.SYNTHETIC_SCRIPTED, pair_id=pair_id,
        pair_role=pair_role, attribution_pair_id=attribution_pair_id, judge_bait=judge_bait,
        judge_bait_kind="bait" if judge_bait else None,
        capability_boundaries=CapabilityBoundaries(transcript_sufficient=True, audio_sufficient=True),
    )
