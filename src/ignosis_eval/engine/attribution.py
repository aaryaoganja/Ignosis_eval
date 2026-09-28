"""Attribution rules (frozen-contract §7, rubric.yaml › attribution_rules; R-10, R-17).

Class-based: agent_speech -> AGENT_BEHAVIOR; non_response -> registration override else perception test;
content_perception -> perception test (no override); timing / tts_render -> PLATFORM_AUDIO;
perception_event -> PERCEPTION (DECLARED_PROVENANCE); not_attributable -> INDETERMINATE.
Perception test only in AUDIO_TRANSCRIPT with platform_live_asr; elsewhere INDETERMINATE
(+ note perception_plausible when our own span confidence is low). Defects are never CUSTOMER_DRIVEN.
Basis: the code's rubric `basis` (PROFILE_DEFAULT) else RUBRIC; PERCEPTION uses DECLARED_PROVENANCE.

`registered` / `material_difference` / `readback_safeguard` come from extraction (B) or are unknown (None,
e.g. for A+, which has no extraction). Unknown inputs never produce a guess.
"""

from __future__ import annotations

from ignosis_eval.contracts.capabilities import perception_allowed
from ignosis_eval.contracts.enums import (
    Attribution,
    AttributionBasis,
    AttributionClass,
    InputMode,
    TranscriptProvenance,
)
from ignosis_eval.contracts.evaluation_record import AttributionResult
from ignosis_eval.spec.registry import CheckDef


def _perception_test(cd: CheckDef, input_mode: InputMode, provenance: TranscriptProvenance | None,
                     material_difference: bool | None, readback_safeguard: bool | None,
                     own_span_confidence_low: bool) -> AttributionResult:
    if perception_allowed(input_mode, provenance) and material_difference is not None:
        if material_difference:
            secondary = Attribution.AGENT_BEHAVIOR if readback_safeguard is False else None
            return AttributionResult(primary=Attribution.PERCEPTION, secondary=secondary,
                                     basis=AttributionBasis.DECLARED_PROVENANCE)
        return AttributionResult(primary=Attribution.AGENT_BEHAVIOR, basis=cd.basis)
    notes = ["perception_plausible"] if own_span_confidence_low else []
    if perception_allowed(input_mode, provenance) and material_difference is None:
        notes.append("perception_test_unavailable")
    return AttributionResult(primary=Attribution.INDETERMINATE, basis=cd.basis, notes=notes)


def attribute(cd: CheckDef, *, input_mode: InputMode, provenance: TranscriptProvenance | None,
              registered: bool | None = None, material_difference: bool | None = None,
              readback_safeguard: bool | None = None, own_span_confidence_low: bool = False) -> AttributionResult:
    cls = cd.attribution_class
    if cls is AttributionClass.AGENT_SPEECH:
        return AttributionResult(primary=Attribution.AGENT_BEHAVIOR, basis=cd.basis)
    if cls is AttributionClass.NON_RESPONSE:
        if registered:
            return AttributionResult(primary=Attribution.AGENT_BEHAVIOR, basis=cd.basis)
        return _perception_test(cd, input_mode, provenance, material_difference, readback_safeguard,
                                own_span_confidence_low)
    if cls is AttributionClass.CONTENT_PERCEPTION:
        return _perception_test(cd, input_mode, provenance, material_difference, readback_safeguard,
                                own_span_confidence_low)
    if cls in (AttributionClass.TIMING, AttributionClass.TTS_RENDER):
        return AttributionResult(primary=Attribution.PLATFORM_AUDIO, basis=cd.basis)
    if cls is AttributionClass.PERCEPTION_EVENT:
        return AttributionResult(primary=Attribution.PERCEPTION, basis=AttributionBasis.DECLARED_PROVENANCE)
    return AttributionResult(primary=Attribution.INDETERMINATE, basis=cd.basis)
