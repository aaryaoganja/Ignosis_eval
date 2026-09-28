"""Closed vocabularies shared by the contracts.

PROVISIONAL: the member lists below were chosen to make the infrastructure executable. Any
vocabulary fixed by the Stage 1-4 specification supersedes these values; reconcile them in one place
(here) and bump the affected schema versions in ignosis_eval/versions.py.
"""

from __future__ import annotations

from enum import StrEnum


class InputMode(StrEnum):
    AUDIO_ONLY = "audio_only"
    TRANSCRIPT_ONLY = "transcript_only"
    AUDIO_TRANSCRIPT = "audio_transcript"


class Speaker(StrEnum):
    AGENT = "agent"
    CUSTOMER = "customer"
    THIRD_PARTY = "third_party"
    SYSTEM = "system"  # IVR prompts, hold messages, recorded disclosures
    UNKNOWN = "unknown"


class TranscriptSource(StrEnum):
    HUMAN_VERBATIM = "human_verbatim"
    HUMAN_CORRECTED_ASR = "human_corrected_asr"
    VENDOR_ASR = "vendor_asr"  # supplied with the call by an upstream system
    PIPELINE_ASR = "pipeline_asr"  # produced by this evaluation pipeline from the audio
    SYNTHETIC_SCRIPT = "synthetic_script"  # authored benchmark script


class TimestampSource(StrEnum):
    ASR_ALIGNED = "asr_aligned"
    HUMAN_ANNOTATED = "human_annotated"
    SCRIPT_PLANNED = "script_planned"
    ESTIMATED = "estimated"
    NONE = "none"


class EvaluabilityIssue(StrEnum):
    """Upstream-observable signals about input quality. These are NOT gold evaluability labels."""

    TRUNCATED_START = "truncated_start"
    TRUNCATED_END = "truncated_end"
    LOW_AUDIO_QUALITY = "low_audio_quality"
    UNINTELLIGIBLE_AUDIO = "unintelligible_audio"
    DIARIZATION_UNRELIABLE = "diarization_unreliable"
    LOW_ASR_CONFIDENCE = "low_asr_confidence"
    NON_TARGET_LANGUAGE = "non_target_language"
    EMPTY_TRANSCRIPT = "empty_transcript"
    MISSING_TIMESTAMPS = "missing_timestamps"
    CROSSTALK = "crosstalk"
    ASR_FAILED = "asr_failed"


class EvaluabilityStatus(StrEnum):
    EVALUABLE = "evaluable"
    OUT_OF_SCOPE = "out_of_scope"  # not a call this evaluator is specified for (e.g. not collections)
    INCONCLUSIVE = "inconclusive"  # in scope, but the available evidence cannot support a verdict


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    OUT_OF_SCOPE = "out_of_scope"


ABSTENTION_VERDICTS: frozenset[Verdict] = frozenset({Verdict.INCONCLUSIVE, Verdict.OUT_OF_SCOPE})

# Verdict <-> evaluability invariant (applies to evaluator records AND gold labels).
EVALUABILITY_TO_VERDICTS: dict[EvaluabilityStatus, frozenset[Verdict]] = {
    EvaluabilityStatus.EVALUABLE: frozenset({Verdict.PASS, Verdict.FAIL}),
    EvaluabilityStatus.OUT_OF_SCOPE: frozenset({Verdict.OUT_OF_SCOPE}),
    EvaluabilityStatus.INCONCLUSIVE: frozenset({Verdict.INCONCLUSIVE}),
}


class GateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NOT_APPLICABLE = "not_applicable"


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class RepairStatus(StrEnum):
    NOT_REPAIRED = "not_repaired"
    PARTIALLY_REPAIRED = "partially_repaired"
    REPAIRED = "repaired"
    NOT_APPLICABLE = "not_applicable"


class AttributionTarget(StrEnum):
    """Which component a defect is attributed to. UNDETERMINED = evidence does not support attribution."""

    AGENT_LOGIC = "agent_logic"
    ASR = "asr"
    TTS = "tts"
    TELEPHONY_AUDIO = "telephony_audio"
    CUSTOMER = "customer"
    UPSTREAM_DATA = "upstream_data"
    UNDETERMINED = "undetermined"


class EvidenceModality(StrEnum):
    TRANSCRIPT = "transcript"
    AUDIO = "audio"
    METADATA = "metadata"  # e.g. call_start_ts for contact-hours checks


class OutcomeClass(StrEnum):
    WIN = "win"
    LOSS = "loss"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class OutcomeCode(StrEnum):
    PROMISE_TO_PAY = "promise_to_pay"
    PAYMENT_COMMITTED_ON_CALL = "payment_committed_on_call"
    PAYMENT_PLAN_AGREED = "payment_plan_agreed"
    DISPUTE_RAISED = "dispute_raised"
    CALLBACK_SCHEDULED = "callback_scheduled"
    REFUSAL_TO_PAY = "refusal_to_pay"
    WRONG_PARTY = "wrong_party"
    HARDSHIP_DISCLOSED = "hardship_disclosed"
    NO_RESOLUTION = "no_resolution"
    CALL_DROPPED = "call_dropped"


class RoutingDecision(StrEnum):
    AUTO_ACCEPT = "auto_accept"
    HUMAN_REVIEW = "human_review"
    COMPLIANCE_ESCALATION = "compliance_escalation"
    NO_ACTION = "no_action"


class EvaluatorArchitecture(StrEnum):
    A = "A"
    A_PLUS = "A+"
    B = "B"
    K0 = "K0"
    MOCK = "MOCK"


class Split(StrEnum):
    DEV = "dev"
    HOLDOUT = "holdout"
    REDTEAM = "redteam"
    CALIBRATION = "calibration"


class SourceKind(StrEnum):
    SYNTHETIC_SCRIPTED = "synthetic_scripted"
    SYNTHETIC_TTS = "synthetic_tts"
    SYNTHETIC_ACTED = "synthetic_acted"
    REAL_REDACTED = "real_redacted"
    REAL_CONSENTED = "real_consented"

    @property
    def is_synthetic(self) -> bool:
        return self.value.startswith("synthetic_")


class Capability(StrEnum):
    """What an input makes observable. Used for modality conformance (see contracts/capabilities.py)."""

    CONTENT = "content"  # spoken content as text (any transcript, incl. pipeline ASR)
    AUDIO = "audio"  # raw audio signal (tone, prosody, overlap, noise)
    TURN_TIMESTAMPS = "turn_timestamps"
    SPEAKER_LABELS = "speaker_labels"
    CALL_START_TS = "call_start_ts"
    CALL_START_CAPTURED = "call_start_captured"  # the opening of the call is present in the input


class LabelConfidence(StrEnum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


class ContestedStatus(StrEnum):
    UNCONTESTED = "uncontested"
    CONTESTED_RESOLVED = "contested_resolved"
    CONTESTED_UNRESOLVED = "contested_unresolved"


class GoldSource(StrEnum):
    AUTHOR_SPECIFIED = "author_specified"
    INDEPENDENT_LABEL = "independent_label"
    ADJUDICATED = "adjudicated"


class LabelerRole(StrEnum):
    AUTHOR = "author"
    LABELER = "labeler"
    ADJUDICATOR = "adjudicator"


class AdjudicationMethod(StrEnum):
    CONSENSUS = "consensus"
    ADJUDICATOR_DECISION = "adjudicator_decision"
    MAJORITY = "majority"
