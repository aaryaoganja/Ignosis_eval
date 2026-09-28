"""Registries consumed by the scorer (scoring-spec SD-01): controls, pairs, twins.

Only the schema is defined here. Registry CONTENT is part of benchmark authoring (B-01); for example the
target check of MP-01 is not fixed by the contract, so no registry entries are shipped by the code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ignosis_eval.contracts._base import Contract, ItemId, NonEmptyStr
from ignosis_eval.contracts.enums import GateId
from ignosis_eval.versions import REGISTRIES_SCHEMA


class ControlEntry(Contract):
    item_id: ItemId
    target_gates: list[GateId] = Field(min_length=1)


class PairEntry(Contract):
    pair_id: NonEmptyStr
    clean_item: ItemId
    violating_item: ItemId
    target_check: NonEmptyStr  # a gate id or an MVP code

    @model_validator(mode="after")
    def _distinct(self) -> "PairEntry":
        if self.clean_item == self.violating_item:
            raise ValueError(f"{self.pair_id}: clean and violating items must differ")
        return self


class TwinEntry(Contract):
    twin_id: NonEmptyStr
    base_item: ItemId
    twin_item: ItemId


class Registries(Contract):
    schema_version: Literal["registries/1.0.0"] = REGISTRIES_SCHEMA
    controls: list[ControlEntry] = Field(default_factory=list)
    pairs: list[PairEntry] = Field(default_factory=list)
    twins: list[TwinEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "Registries":
        for label, ids in (("control", [c.item_id for c in self.controls]),
                           ("pair", [p.pair_id for p in self.pairs]),
                           ("twin", [t.twin_id for t in self.twins])):
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} registry entries")
        return self

    def control_targets(self, item_id: str) -> list[GateId]:
        return next((c.target_gates for c in self.controls if c.item_id == item_id), [])

    def item_ids(self) -> set[str]:
        ids = {c.item_id for c in self.controls}
        for p in self.pairs:
            ids |= {p.clean_item, p.violating_item}
        for t in self.twins:
            ids |= {t.base_item, t.twin_item}
        return ids
