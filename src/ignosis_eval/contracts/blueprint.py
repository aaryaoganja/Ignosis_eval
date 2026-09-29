"""Gold-blueprint contract for the frozen DEV design (`bench/public/dev-gold-blueprint.yaml`).

`gold_blueprint/1.1.0` implements the final (authoritative) blueprint: every item carries `rule_basis` (contract §0 /
BD decision ids, e.g. SC-04) and `external_dependencies` (implementation-blockers B-xx ids). The `depends_on` field
named by the frozen `gold-blueprint-schema.yaml` (`gold_blueprint/1.0.0`) is superseded (BD-05,
docs/bd-changelog.md) and rejected here. The blueprint is design intent, never gold.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ignosis_eval.versions import GOLD_BLUEPRINT_SCHEMA

RULE_BASIS_PATTERN = r"^(R|AJ|SC|BD)-\d{2}$"
EXTERNAL_DEPENDENCY_PATTERN = r"^B-\d{2}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlueprintGate(_Strict):
    status: Literal["PASS", "FAIL", "NA", "INCONCLUSIVE", "INCONCLUSIVE+trigger", "OUT_OF_SCOPE"]
    label_type: str


class BlueprintFinding(_Strict):
    code: str
    severity: str
    action: str
    anchor_beat: str
    evidence_elements: str
    attribution_T: str
    label_type: str
    note: str


class BlueprintOutcome(_Strict):
    dispositions: str
    positive: bool
    outcome_attribution: str


class BlueprintItem(_Strict):
    item_id: str
    pack: str
    split: str
    modes: str
    header: str | None
    gates: dict[str, BlueprintGate] | Literal["n/a"]
    findings: list[BlueprintFinding]
    verdict: str
    evaluability: str
    outcome: BlueprintOutcome
    dangerous_win: str
    clean_loss: bool
    attribution_summary: str
    control_target_gates: list[str]
    abstention_targets: list[Any]
    contested: bool
    label_confidence: str
    ambiguity_notes: str
    rule_basis: list[Annotated[str, Field(pattern=RULE_BASIS_PATTERN)]]
    external_dependencies: list[Annotated[str, Field(pattern=EXTERNAL_DEPENDENCY_PATTERN)]]


class BlueprintSchema(_Strict):
    gold_blueprint_version: str
    principle: str
    anchors: str
    per_item_fields: dict[str, str]
    label_type_legend: dict[str, str]
    mode_derivation: str


class Blueprint(_Strict):
    """The whole `dev-gold-blueprint.yaml` (its `schema:` block plus the items)."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"x-schema-version": GOLD_BLUEPRINT_SCHEMA})
    schema_block: BlueprintSchema = Field(alias="schema")
    items: list[BlueprintItem]


class SchemaFile(_Strict):
    schema_block: BlueprintSchema = Field(alias="schema")


__all__ = ["Blueprint", "BlueprintFinding", "BlueprintGate", "BlueprintItem", "BlueprintOutcome", "BlueprintSchema",
           "EXTERNAL_DEPENDENCY_PATTERN", "RULE_BASIS_PATTERN", "SchemaFile"]
