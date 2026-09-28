"""Capability table resolver for gold derivation and the scorer's H6 check (frozen-contract §8).

EVALUABLE / OUT_OF_SCOPE come from each check's `applicable_modes` in rubric.yaml. CONDITIONAL entries
are resolved from the rows of the §8 table itself:
  G7                 "Header call_start_ts only"  (absent -> OUT_OF_SCOPE, EXTERNAL_DATA_REQUIRED)
  PLT-01 TRANSCRIPT  "Only if the transcript has timestamps"
  PLT-03 / PLT-04    "Only with platform_live_asr"
Any other CONDITIONAL entry fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CapabilityError(RuntimeError):
    pass


_SECTION_ROWS = {
    "G7": "header",
    "PLT-01": "timestamps",
    "PLT-03": "platform_live_asr",
    "PLT-04": "platform_live_asr",
}


@dataclass(frozen=True)
class CapabilityTable:
    applicability: dict[str, dict[str, str]]  # check id -> {input_mode: EVALUABLE | OUT_OF_SCOPE | CONDITIONAL}

    @classmethod
    def from_rubric(cls, rubric: dict[str, Any]) -> "CapabilityTable":
        default = dict(rubric["default_applicable_modes"])
        table: dict[str, dict[str, str]] = {}
        for section in ("gates", "codes", "platform_signals"):
            for c in rubric[section]:
                table[c["id"]] = dict(c.get("applicable_modes") or default)
        for cid, modes in table.items():
            for mode, app in modes.items():
                if app == "CONDITIONAL" and cid not in _SECTION_ROWS:
                    raise CapabilityError(f"no §8 rule resolves CONDITIONAL {cid}/{mode} (fail closed)")
        return cls(table)

    def in_scope(self, check_id: str, input_mode: str, *, has_call_start_ts: bool, has_timestamps: bool,
                 provenance: str | None) -> tuple[bool, str | None]:
        """(evaluable?, oos_reason) for one unit."""
        try:
            app = self.applicability[check_id][input_mode]
        except KeyError as exc:
            raise CapabilityError(f"unknown check/mode {check_id}/{input_mode}") from exc
        if app == "EVALUABLE":
            return True, None
        if app == "OUT_OF_SCOPE":
            return False, "MODE_CAPABILITY"
        row = _SECTION_ROWS[check_id]
        if row == "header":
            return (True, None) if has_call_start_ts else (False, "EXTERNAL_DATA_REQUIRED")
        if row == "timestamps":
            return (True, None) if has_timestamps else (False, "MODE_CAPABILITY")
        return (True, None) if provenance == "platform_live_asr" else (False, "MODE_CAPABILITY")


def perception_allowed(input_mode: str, provenance: str | None) -> bool:
    """§7 / §8: PERCEPTION only in AUDIO_TRANSCRIPT with platform_live_asr."""
    return input_mode == "AUDIO_TRANSCRIPT" and provenance == "platform_live_asr"
