"""Evaluation Profile contract: the declared taxonomy (gates, dimensions, defects) and capability map.

The profile is a versioned, hashed artifact recorded in every run manifest. The scorer consumes it only
for declared-contract checks (unknown ids, modality conformance) — never for evaluator internals.

NOTE: the profile shipped in config/profiles/ is a PLACEHOLDER (status: placeholder). It exists so the
infrastructure can execute end-to-end; it is not the Stage 1-3 rubric.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from ignosis_eval.canonical import canonical_sha256
from ignosis_eval.contracts._base import Contract, Id, NonEmptyStr
from ignosis_eval.contracts.enums import Capability, OutcomeClass, OutcomeCode, Severity
from ignosis_eval.versions import PROFILE_SCHEMA


class GateDef(Contract):
    gate_id: Id
    title: NonEmptyStr
    description: NonEmptyStr
    severity: Severity = Severity.CRITICAL
    requires: list[Capability] = Field(default_factory=list)


class DimensionDef(Contract):
    dimension_id: Id
    title: NonEmptyStr
    description: NonEmptyStr
    scale_min: float = 1.0
    scale_max: float = 5.0
    requires: list[Capability] = Field(default_factory=list)


class DefectDef(Contract):
    defect_id: Id
    title: NonEmptyStr
    description: NonEmptyStr
    severity: Severity
    gate_id: Id | None = None
    dimension_id: Id | None = None
    requires: list[Capability] = Field(default_factory=list)


class Profile(Contract):
    schema_version: Literal["profile/1.0.0"] = PROFILE_SCHEMA
    profile_id: Id
    profile_version: NonEmptyStr
    rubric_version: NonEmptyStr
    status: Literal["placeholder", "draft", "frozen"]
    use_case: Literal["collections"] = "collections"
    description: NonEmptyStr
    gates: list[GateDef]
    dimensions: list[DimensionDef] = Field(default_factory=list)
    defects: list[DefectDef]
    outcome_classes: dict[OutcomeCode, OutcomeClass]

    @model_validator(mode="after")
    def _refs(self) -> "Profile":
        errs: list[str] = []
        for label, ids in (
            ("gate", [g.gate_id for g in self.gates]),
            ("dimension", [d.dimension_id for d in self.dimensions]),
            ("defect", [d.defect_id for d in self.defects]),
        ):
            if len(ids) != len(set(ids)):
                errs.append(f"duplicate {label} ids")
        gate_ids = {g.gate_id for g in self.gates}
        dim_ids = {d.dimension_id for d in self.dimensions}
        for d in self.defects:
            if d.gate_id and d.gate_id not in gate_ids:
                errs.append(f"defect {d.defect_id} references unknown gate {d.gate_id}")
            if d.dimension_id and d.dimension_id not in dim_ids:
                errs.append(f"defect {d.defect_id} references unknown dimension {d.dimension_id}")
        missing = set(OutcomeCode) - set(self.outcome_classes)
        if missing:
            errs.append(f"outcome_classes missing codes: {sorted(m.value for m in missing)}")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    def gate_def(self, gate_id: str) -> GateDef | None:
        return next((g for g in self.gates if g.gate_id == gate_id), None)

    def dimension_def(self, dimension_id: str) -> DimensionDef | None:
        return next((d for d in self.dimensions if d.dimension_id == dimension_id), None)

    def defect_def(self, defect_id: str) -> DefectDef | None:
        return next((d for d in self.defects if d.defect_id == defect_id), None)


def load_profile(path: str | Path) -> tuple[Profile, str]:
    """Load a profile YAML; returns (profile, sha256 of the canonical JSON form)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    profile = Profile.model_validate(data)
    return profile, canonical_sha256(profile.to_json_dict())
