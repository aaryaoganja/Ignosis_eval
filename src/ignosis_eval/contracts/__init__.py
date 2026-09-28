"""Typed, versioned data contracts. This subpackage depends only on ignosis_eval.canonical/versions."""

from ignosis_eval.contracts.canonical_input import (
    AudioMetadata,
    AudioRendering,
    CanonicalInput,
    EvaluabilityMetadata,
    EvidenceAvailability,
    LanguageInfo,
    Transcript,
    TranscriptProvenance,
    Turn,
    derive_evidence_availability,
)
from ignosis_eval.contracts.evaluation_record import (
    AttributionClaim,
    ConfidenceSummary,
    DimensionResult,
    EvaluabilityAssessment,
    EvaluationRecord,
    EvaluatorInfo,
    EvidenceItem,
    ExperimentMetadata,
    Finding,
    GateResult,
    ObservableOutcomes,
    Routing,
)
from ignosis_eval.contracts.profile import DefectDef, DimensionDef, GateDef, Profile, load_profile

__all__ = [
    "AttributionClaim", "AudioMetadata", "AudioRendering", "CanonicalInput", "ConfidenceSummary",
    "DefectDef", "DimensionDef", "DimensionResult", "EvaluabilityAssessment", "EvaluabilityMetadata",
    "EvaluationRecord", "EvaluatorInfo", "EvidenceAvailability", "EvidenceItem", "ExperimentMetadata",
    "Finding", "GateDef", "GateResult", "LanguageInfo", "ObservableOutcomes", "Profile", "Routing",
    "Transcript", "TranscriptProvenance", "Turn", "derive_evidence_availability", "load_profile",
]
