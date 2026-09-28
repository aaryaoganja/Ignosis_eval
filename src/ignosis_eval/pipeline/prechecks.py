"""Deterministic pre-checks and evaluability (rubric.yaml › evaluability_checks, precheck_codes; §9).

Implemented deterministically now:
  DC-00 role gate (min role confidence < profile.thresholds.role_confidence_min -> NOT_EVALUABLE),
  DC-01 transcript reliability markers (span-level), DC-TRUNC via the header,
  G7 calling window (header call_start_ts only; OUT_OF_SCOPE in AUDIO or without a header, R-08),
  G8 / G9 / POL-01b from the profile lists (empty in collections_default_v1 -> G8/G9 NA, POL-01b silent).
Recorded as `pending_signoff` (no value is chosen):
  DC-01 audio (asr_* thresholds), DC-02 non-conversation (non_conversation_min_borrower_words; voicemail
  cues empty), DC-03 / G1c (depends on DC-02), DC-LANG (detection method).
Recorded as `not_implemented` (next build phase):
  DC-DIV cross-source divergence (needs the normalizer and extraction), in-text truncation marker
  (the marker syntax is not defined in §2.3).
PARTIAL evaluability is never emitted: the spec lists the value but does not define when it applies.
"""

from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

from ignosis_eval.contracts.canonical_input import FrontendCheck, FrontendResult, TranscriptHeader, Turn
from ignosis_eval.contracts.enums import (
    ActionType,
    Attribution,
    AttributionBasis,
    Confidence,
    CriticalStatus,
    EvaluabilityStatus,
    FindingState,
    GateId,
    GateStatus,
    InputMode,
    OosReason,
    ReasonCode,
    Role,
    Severity,
)
from ignosis_eval.contracts.evaluation_record import AttributionResult, Evidence, Finding, GateResult
from ignosis_eval.contracts.evidence import norm
from ignosis_eval.spec.loader import Spec


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def precheck_g7(header: TranscriptHeader, input_mode: InputMode, spec: Spec) -> GateResult:
    attr = AttributionResult(primary=Attribution.INDETERMINATE, basis=AttributionBasis.PROFILE_DEFAULT)
    if input_mode is InputMode.AUDIO:
        return GateResult(gate=GateId.G7, status=GateStatus.OUT_OF_SCOPE, oos_reason=OosReason.MODE_CAPABILITY,
                          note="AUDIO mode: audio metadata is never used as call time (R-08)")
    if header.call_start_ts is None:
        return GateResult(gate=GateId.G7, status=GateStatus.OUT_OF_SCOPE,
                          oos_reason=OosReason.EXTERNAL_DATA_REQUIRED, reason_codes=[ReasonCode.EXTERNAL_DATA_REQUIRED],
                          note="no header call_start_ts")
    win = spec.profile["calling_window"]
    local = header.call_start_ts.astimezone(ZoneInfo(win["timezone"])).time()
    inside = _hhmm(win["start"]) <= local < _hhmm(win["end"])  # boundary convention: see spec-reconciliation
    ev = [Evidence(header_field="call_start_ts")]
    if inside:
        return GateResult(gate=GateId.G7, status=GateStatus.PASS, confidence=Confidence.HIGH, evidence=ev,
                          attribution=attr)
    return GateResult(gate=GateId.G7, status=GateStatus.FAIL, critical_status=CriticalStatus.CONFIRMED,
                      confidence=Confidence.HIGH, evidence=ev, attribution=attr)


def precheck_g8(spec: Spec) -> tuple[GateResult | None, FrontendCheck]:
    if not spec.profile["disclosures"].get("gates"):
        return (GateResult(gate=GateId.G8, status=GateStatus.NA, note="profile lists no disclosure gates"),
                FrontendCheck(check="G8", status="done"))
    return None, FrontendCheck(check="G8", status="not_implemented",
                               note="non-empty disclosure gates need J-G8P (B build phase)")


def precheck_g9(turns: list[Turn], spec: Spec) -> GateResult:
    phrases = spec.profile["prohibited_phrases"].get("gates") or []
    if not phrases:
        return GateResult(gate=GateId.G9, status=GateStatus.NA, note="profile lists no prohibited-phrase gates")
    for t in turns:
        if t.role is Role.AGENT:
            for ph in phrases:
                if norm(ph) and norm(ph) in norm(t.text):
                    return GateResult(gate=GateId.G9, status=GateStatus.FAIL, critical_status=CriticalStatus.CONFIRMED,
                                      confidence=Confidence.HIGH,
                                      evidence=[Evidence(turn=t.turn, quote=ph, role=Role.AGENT)],
                                      attribution=AttributionResult(primary=Attribution.AGENT_BEHAVIOR,
                                                                    basis=AttributionBasis.PROFILE_DEFAULT))
    return GateResult(gate=GateId.G9, status=GateStatus.PASS, confidence=Confidence.HIGH)


def precheck_pol01b(turns: list[Turn], spec: Spec) -> list[Finding]:
    phrases = spec.profile["prohibited_phrases"].get("non_gate") or []
    out: list[Finding] = []
    for t in turns:
        if t.role is not Role.AGENT:
            continue
        for ph in phrases:
            if norm(ph) and norm(ph) in norm(t.text):
                out.append(Finding(code="POL-01", sub_rule="POL-01b", severity=Severity.MAJOR,
                                   confidence=Confidence.HIGH, action_type=ActionType.FIX,
                                   finding_state=FindingState.ASSERTED, anchor_turn=t.turn,
                                   attribution=AttributionResult(primary=Attribution.AGENT_BEHAVIOR,
                                                                 basis=AttributionBasis.PROFILE_DEFAULT),
                                   evidence=[Evidence(turn=t.turn, quote=ph, role=Role.AGENT)]))
    return out


def run_frontend(turns: list[Turn], header: TranscriptHeader, input_mode: InputMode, spec: Spec, *,
                 role_mapping_confidence: float = 1.0, diarized: bool = False) -> FrontendResult:
    steps: list[FrontendCheck] = []
    reasons: list[ReasonCode] = []
    status = EvaluabilityStatus.EVALUABLE

    # DC-00 (AJ-05): call-level role mapping only; no agent or no borrower turn -> NOT_EVALUABLE
    role_min = float(spec.threshold("role_confidence_min"))
    roles = {t.role for t in turns}
    if role_mapping_confidence < role_min or Role.AGENT not in roles or Role.BORROWER not in roles:
        status = EvaluabilityStatus.NOT_EVALUABLE
        reasons.append(ReasonCode.ROLE_UNCERTAIN)
    steps.append(FrontendCheck(check="DC-00", status="done"))

    steps.append(FrontendCheck(check="DC-01-transcript-markers", status="done"))
    steps.append(FrontendCheck(check="DC-01-unknown-role-turns", status="done",
                               note="UNKNOWN-labeled turns are span-unreliable (AJ-05)"))
    if diarized:
        steps.append(FrontendCheck(check="DC-01-diarization-turn", status="pending_signoff", blocker="B-06/B-11",
                                   note="diarization_turn_min_confidence is PENDING (AJ-05)"))
    if input_mode in (InputMode.AUDIO, InputMode.AUDIO_TRANSCRIPT):
        steps.append(FrontendCheck(check="DC-01-audio", status="pending_signoff", blocker="B-06/B-11",
                                   note="asr_low_confidence_word / asr_unreliable_call_share are PENDING"))
    steps.append(FrontendCheck(check="DC-02", status="pending_signoff", blocker="B-11/B-04",
                               note="non_conversation_min_borrower_words PENDING; voicemail_cues terms empty"))
    steps.append(FrontendCheck(check="DC-03/G1c", status="pending_signoff", blocker="B-11/B-04",
                               note="depends on DC-02"))
    steps.append(FrontendCheck(check="DC-LANG", status="pending_signoff", blocker="B-04/B-11"))
    if header.truncated_start:
        reasons.append(ReasonCode.TRANSCRIPT_TRUNCATED)
    steps.append(FrontendCheck(check="DC-TRUNC-header", status="done"))
    steps.append(FrontendCheck(check="DC-TRUNC-in-text-marker", status="not_implemented",
                               note="in-text truncation marker syntax is not defined in §2.3"))
    if input_mode is InputMode.AUDIO_TRANSCRIPT:
        steps.append(FrontendCheck(check="DC-DIV", status="not_implemented",
                                   note="needs the deterministic normalizer (B-04) and extraction"))
    steps.append(FrontendCheck(check="EVALUABILITY-PARTIAL", status="not_applicable",
                               note="PARTIAL is derived after evaluation by the verdict engine (V7, AJ-04)"))

    prechecks = [precheck_g7(header, input_mode, spec)]
    steps.append(FrontendCheck(check="G7", status="done"))
    g8, g8_step = precheck_g8(spec)
    steps.append(g8_step)
    if g8 is not None:
        prechecks.append(g8)
    prechecks.append(precheck_g9(turns, spec))
    steps.append(FrontendCheck(check="G9", status="done"))
    pol = precheck_pol01b(turns, spec)
    steps.append(FrontendCheck(check="POL-01b", status="done"))
    return FrontendResult(evaluability_status=status, reason_codes=reasons, prechecks=prechecks,
                          precheck_findings=pol, steps=steps)
