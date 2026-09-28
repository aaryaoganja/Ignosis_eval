"""Unit-level capability helpers (frozen-contract.md §8), resolved through the rubric's applicable_modes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ignosis_eval.contracts.benchmark import UnitFacts
from ignosis_eval.contracts.enums import InputMode, ModeApplicability, OosReason, TranscriptProvenance

if TYPE_CHECKING:  # pragma: no cover
    from ignosis_eval.spec.registry import Registry


def unit_mode_status(registry: "Registry", check_id: str, facts: UnitFacts) -> tuple[ModeApplicability,
                                                                                    OosReason | None]:
    return registry.mode_status(check_id, facts.input_mode, has_call_start_ts=facts.has_call_start_ts,
                                has_timestamps=facts.has_timestamps, provenance=facts.provenance)


def oos_checks_for_unit(registry: "Registry", facts: UnitFacts) -> dict[str, OosReason]:
    """Every MVP check (gates, codes, PLT) the unit's mode cannot evaluate, with the OOS reason."""
    out: dict[str, OosReason] = {}
    for cid in registry.checks:
        app, reason = unit_mode_status(registry, cid, facts)
        if app is ModeApplicability.OUT_OF_SCOPE:
            out[cid] = reason or OosReason.MODE_CAPABILITY
    return out


def perception_allowed(input_mode: InputMode, provenance: TranscriptProvenance | None) -> bool:
    """R-17: PERCEPTION attribution only in AUDIO_TRANSCRIPT with declared platform_live_asr."""
    return input_mode is InputMode.AUDIO_TRANSCRIPT and provenance is TranscriptProvenance.PLATFORM_LIVE_ASR
