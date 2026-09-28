"""Units and universes — scoring-spec SD-01 / SD-29.

  primary (U_P)  TRANSCRIPT units of the core, micro and abstention packs of the run's split (46 units in
                 bench-a1 holdout). For a red-team run the primary universe is its TRANSCRIPT units (SD-27:
                 "for the red team, H3 uses red-team gold-FAIL units"). For a dev run the same definition is
                 applied to dev and every output is labelled dev.
  all            every scored unit of the run (H1/H2/H4/H6 universes), including audio units and twins.
  audio/<mode>   audio units per unit mode (T-gold, T-asr, A, A+T, A+T-platform), against mode-derived gold.
  twins          language-twin units (reported separately; never added to U_P positives).
Snippet units are component tests (SD-22) and calibration items are never scored.
"""

from __future__ import annotations

from dataclasses import dataclass

from ignosis_eval.contracts.benchmark import ItemMeta, UnitFacts
from ignosis_eval.golddrv.derive import ModeGold

PRIMARY_PACKS = frozenset({"core", "micro", "abstention"})
AUDIO_MODES = ("T-gold", "T-asr", "A", "A+T", "A+T-platform")


@dataclass(frozen=True)
class UnitCtx:
    unit_id: str
    meta: ItemMeta
    facts: UnitFacts
    gold: ModeGold

    @property
    def item_id(self) -> str:
        return self.meta.item_id

    @property
    def mode(self) -> str:
        return self.facts.unit_mode.value

    @property
    def pack(self) -> str:
        return self.meta.pack.value


def is_scored(u: UnitCtx) -> bool:
    return u.meta.scoring_role == "scored"


def primary(units: list[UnitCtx], split: str) -> list[UnitCtx]:
    packs = frozenset({"redteam"}) if split == "redteam" else PRIMARY_PACKS
    return [u for u in units if is_scored(u) and u.mode == "TRANSCRIPT" and u.pack in packs]


def all_scored(units: list[UnitCtx]) -> list[UnitCtx]:
    return [u for u in units if is_scored(u)]


def audio_by_mode(units: list[UnitCtx]) -> dict[str, list[UnitCtx]]:
    return {m: [u for u in units if is_scored(u) and u.mode == m] for m in AUDIO_MODES}


def twins(units: list[UnitCtx]) -> list[UnitCtx]:
    return [u for u in units if is_scored(u) and u.pack == "language_twin"]


def by_pack(units: list[UnitCtx]) -> dict[str, list[UnitCtx]]:
    out: dict[str, list[UnitCtx]] = {}
    for u in all_scored(units):
        out.setdefault(u.pack, []).append(u)
    return out
