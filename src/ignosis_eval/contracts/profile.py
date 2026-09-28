"""Structural model of docs/spec/profile.yaml (PENDING-aware). Validates shape only; values stay as frozen.

Loading a profile never substitutes a default for a PENDING_HUMAN_SIGNOFF value (see spec/loader.py).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PENDING = "PENDING_HUMAN_SIGNOFF"


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ThresholdSpec(_Open):
    value: float | int | Literal["PENDING_HUMAN_SIGNOFF"]
    status: str | None = None
    decision_required: str | None = None
    note: str | None = None


class CallingWindow(_Open):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    timezone: str
    requires: str


class Offers(_Open):
    not_ai_authorized: list[str]
    authority_unknown: list[str]
    allowed: list[str]


class Consequences(_Open):
    permitted_categories: list[str]
    prohibited_categories: dict[str, Literal["G2", "G3"]]


class ProfileSpec(_Open):
    profile_id: str
    profile_version: str
    rubric_ref: str
    use_case: Literal["collections"]
    identity_verification: dict[str, Any]
    asks: dict[str, Any]
    ptp: dict[str, Any]
    offers: Offers
    consequences: Consequences
    rights: dict[str, Any]
    vulnerability_protocol: dict[str, Any]
    escalation_triggers: dict[str, Any]
    disclosures: dict[str, Any]
    prohibited_phrases: dict[str, Any]
    calling_window: CallingWindow
    languages_supported: list[str]
    scenario_paths: dict[str, Any]
    thresholds: dict[str, ThresholdSpec]
    lexicons: dict[str, Any]
