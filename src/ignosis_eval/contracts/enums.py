"""Closed vocabularies — transcribed from docs/spec/rubric.yaml › enums (FROZEN, rubric 1.0-mvp).

Every enum mirrored from the rubric is checked against the YAML by tests/test_spec.py::test_enums_match_rubric,
so any drift between this module and the frozen rubric fails the test suite. Enums that are not in
`rubric.yaml › enums` cite their source section.
"""

from __future__ import annotations

from enum import StrEnum


# ------------------------------------------------------------------------------ rubric.yaml › enums
class InputMode(StrEnum):
    AUDIO = "AUDIO"
    TRANSCRIPT = "TRANSCRIPT"
    AUDIO_TRANSCRIPT = "AUDIO_TRANSCRIPT"


class TranscriptProvenance(StrEnum):
    PLATFORM_LIVE_ASR = "platform_live_asr"
    HUMAN = "human"
    OFFLINE_ASR = "offline_asr"
    UNKNOWN = "unknown"


class CheckStatus(StrEnum):
    PASS = "PASS"
    DEFECT = "DEFECT"
    NA = "NA"
    INCONCLUSIVE = "INCONCLUSIVE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "NA"
    INCONCLUSIVE = "INCONCLUSIVE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class CriticalStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    SUSPECTED = "SUSPECTED"


class DimensionStatus(StrEnum):
    CLEAN = "CLEAN"
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    POSSIBLE = "POSSIBLE"
    INCONCLUSIVE = "INCONCLUSIVE"
    NA = "NA"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Verdict(StrEnum):
    CRITICAL_FAIL = "CRITICAL_FAIL"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    MEETS_BAR = "MEETS_BAR"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class RecordStatus(StrEnum):
    OK = "OK"
    EVALUATION_FAILED = "EVALUATION_FAILED"


class EvaluabilityStatus(StrEnum):
    EVALUABLE = "EVALUABLE"
    PARTIAL = "PARTIAL"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    INFORMATIONAL = "INFORMATIONAL"


class RepairStatus(StrEnum):
    REPAIRED = "REPAIRED"
    UNREPAIRED = "UNREPAIRED"


class FindingState(StrEnum):
    ASSERTED = "ASSERTED"
    POSSIBLE = "POSSIBLE"  # Major with LOW confidence


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


CONFIDENCE_RANK: dict[Confidence, int] = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}


def min_confidence(*levels: Confidence) -> Confidence:
    return min(levels, key=lambda c: CONFIDENCE_RANK[c])


class ActionType(StrEnum):
    REVIEW = "REVIEW"
    REMEDIATE = "REMEDIATE"
    FIX = "FIX"


class Method(StrEnum):
    DET = "DET"
    LLM_EXTRACT = "LLM_EXTRACT"
    LLM_JUDGE = "LLM_JUDGE"
    AUDIO = "AUDIO"
    HUMAN = "HUMAN"


class Attribution(StrEnum):
    AGENT_BEHAVIOR = "AGENT_BEHAVIOR"
    PERCEPTION = "PERCEPTION"
    PLATFORM_AUDIO = "PLATFORM_AUDIO"
    CUSTOMER_DRIVEN = "CUSTOMER_DRIVEN"  # outcomes only; defects are never CUSTOMER_DRIVEN (§7)
    INDETERMINATE = "INDETERMINATE"


class AttributionBasis(StrEnum):
    RUBRIC = "RUBRIC"
    PROFILE_DEFAULT = "PROFILE_DEFAULT"
    DECLARED_PROVENANCE = "DECLARED_PROVENANCE"


class AttributionClass(StrEnum):
    AGENT_SPEECH = "agent_speech"
    NON_RESPONSE = "non_response"
    CONTENT_PERCEPTION = "content_perception"
    TIMING = "timing"
    TTS_RENDER = "tts_render"
    PERCEPTION_EVENT = "perception_event"
    NOT_ATTRIBUTABLE = "not_attributable"


class MvpStatus(StrEnum):
    MVP = "MVP"
    NOT_EVALUATED_IN_MVP = "NOT_EVALUATED_IN_MVP"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class ModeApplicability(StrEnum):
    EVALUABLE = "EVALUABLE"
    CONDITIONAL = "CONDITIONAL"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class OosReason(StrEnum):
    EXTERNAL_DATA_REQUIRED = "EXTERNAL_DATA_REQUIRED"
    MODE_CAPABILITY = "MODE_CAPABILITY"


class DangerousWin(StrEnum):
    NONE = "NONE"
    CRITICAL = "CRITICAL"
    MATERIAL = "MATERIAL"


class OutcomeAttribution(StrEnum):
    AGENT_DRIVEN = "AGENT_DRIVEN"
    CUSTOMER_DRIVEN = "CUSTOMER_DRIVEN"
    POLICY_DRIVEN = "POLICY_DRIVEN"
    INDETERMINATE = "INDETERMINATE"


class ReasonCode(StrEnum):
    ROLE_UNCERTAIN = "ROLE_UNCERTAIN"
    TRANSCRIPT_UNRELIABLE = "TRANSCRIPT_UNRELIABLE"
    AUDIO_POOR = "AUDIO_POOR"
    SPAN_UNRELIABLE = "SPAN_UNRELIABLE"
    NON_CONVERSATIONAL = "NON_CONVERSATIONAL"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    POLICY_UNKNOWN = "POLICY_UNKNOWN"
    EXTERNAL_DATA_REQUIRED = "EXTERNAL_DATA_REQUIRED"
    LANGUAGE_UNSUPPORTED = "LANGUAGE_UNSUPPORTED"
    TRANSCRIPT_TRUNCATED = "TRANSCRIPT_TRUNCATED"
    STAGE_UNKNOWN = "STAGE_UNKNOWN"


# name in rubric.yaml › enums -> Python enum (used by the drift test)
RUBRIC_ENUMS: dict[str, type[StrEnum]] = {
    "input_mode": InputMode,
    "transcript_provenance": TranscriptProvenance,
    "check_status": CheckStatus,
    "gate_status": GateStatus,
    "critical_status": CriticalStatus,
    "dimension_status": DimensionStatus,
    "verdict": Verdict,
    "record_status": RecordStatus,
    "evaluability_status": EvaluabilityStatus,
    "severity": Severity,
    "repair_status": RepairStatus,
    "finding_state": FindingState,
    "confidence": Confidence,
    "action_type": ActionType,
    "method": Method,
    "attribution": Attribution,
    "attribution_basis": AttributionBasis,
    "attribution_class": AttributionClass,
    "mvp_status": MvpStatus,
    "mode_applicability": ModeApplicability,
    "oos_reason": OosReason,
    "dangerous_win": DangerousWin,
    "outcome_attribution": OutcomeAttribution,
    "reason_code": ReasonCode,
}


# ------------------------------------------------------------------------------ other spec sources
class Role(StrEnum):
    """frozen-contract.md §2.3 role labels (CUSTOMER is an accepted alias of BORROWER at parse time)."""

    AGENT = "AGENT"
    BORROWER = "BORROWER"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class Disposition(StrEnum):
    """rubric.yaml › outcome_model.observable_dispositions."""

    ACKNOWLEDGED = "ACKNOWLEDGED"
    PTP_STATED = "PTP_STATED"
    PAYMENT_CLAIMED_ALREADY_PAID = "PAYMENT_CLAIMED_ALREADY_PAID"
    PAYMENT_CLAIMED_IN_CALL = "PAYMENT_CLAIMED_IN_CALL"
    DISPUTE_RAISED = "DISPUTE_RAISED"
    HARDSHIP_OR_INABILITY_STATED = "HARDSHIP_OR_INABILITY_STATED"
    SETTLEMENT_REQUESTED = "SETTLEMENT_REQUESTED"
    OFFER_AGREED = "OFFER_AGREED"
    MANDATE_AGREED = "MANDATE_AGREED"
    CALLBACK_AGREED = "CALLBACK_AGREED"
    HUMAN_REQUESTED = "HUMAN_REQUESTED"
    TRANSFER_ANNOUNCED = "TRANSFER_ANNOUNCED"
    REFUSED = "REFUSED"
    STOP_REQUESTED = "STOP_REQUESTED"
    THIRD_PARTY_REACHED = "THIRD_PARTY_REACHED"
    DEATH_REPORTED = "DEATH_REPORTED"
    COMPLAINT_RAISED = "COMPLAINT_RAISED"
    INCOMPLETE = "INCOMPLETE"
    NON_CONVERSATIONAL = "NON_CONVERSATIONAL"


class Firmness(StrEnum):
    """rubric.yaml › extraction_vocabulary.firmness."""

    FIRM = "firm"
    SOFT = "soft"
    CONDITIONAL = "conditional"


class GateId(StrEnum):
    """rubric.yaml › gates."""

    G1 = "G1"
    G2 = "G2"
    G3 = "G3"
    G4 = "G4"
    G5 = "G5"
    G6 = "G6"
    G7 = "G7"
    G8 = "G8"
    G9 = "G9"


GATE_IDS: tuple[GateId, ...] = tuple(GateId)


class Split(StrEnum):
    """experiment-protocol.md P-1 (dev in-repo; holdout and red team under $BENCH_PRIVATE_DIR)."""

    DEV = "dev"
    HOLDOUT = "holdout"
    REDTEAM = "redteam"


class Pack(StrEnum):
    """frozen-contract.md §12 packs."""

    CORE = "core"
    MICRO = "micro"
    ABSTENTION = "abstention"
    MODALITY = "modality"
    LANGUAGE_TWIN = "language_twin"
    SNIPPET = "snippet"
    REDTEAM = "redteam"
    CALIBRATION = "calibration"


# Packs whose TRANSCRIPT units form the primary universe U_P (scoring-spec SD-01).
PRIMARY_PACKS: frozenset[Pack] = frozenset({Pack.CORE, Pack.MICRO, Pack.ABSTENTION})


class UnitMode(StrEnum):
    """Unit modes (experiment-protocol P-5). Directory-safe spelling of `A+T(platform)` is `A+T-platform`."""

    TRANSCRIPT = "TRANSCRIPT"  # textual items (core, micro, abstention, twins, red team)
    T_GOLD = "T-gold"  # audio item, gold transcript supplied as TRANSCRIPT
    T_ASR = "T-asr"  # audio item, our cached ASR supplied as TRANSCRIPT (provenance offline_asr)
    A = "A"  # audio only
    A_T = "A+T"  # audio + supplied transcript
    A_T_PLATFORM = "A+T-platform"  # audio + weaker-ASR transcript, provenance platform_live_asr


UNIT_MODE_INPUT: dict[UnitMode, InputMode] = {
    UnitMode.TRANSCRIPT: InputMode.TRANSCRIPT,
    UnitMode.T_GOLD: InputMode.TRANSCRIPT,
    UnitMode.T_ASR: InputMode.TRANSCRIPT,
    UnitMode.A: InputMode.AUDIO,
    UnitMode.A_T: InputMode.AUDIO_TRANSCRIPT,
    UnitMode.A_T_PLATFORM: InputMode.AUDIO_TRANSCRIPT,
}


class System(StrEnum):
    """frozen-contract.md §11."""

    K0 = "K0"
    A = "A"
    A_PLUS = "A+"
    B = "B"
