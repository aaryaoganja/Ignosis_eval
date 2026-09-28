"""Check registry built from rubric.yaml: gates, MVP codes, platform signals, OOS / not-evaluated lists,
named sets, and the mode capability table (frozen-contract.md §8 via each check's `applicable_modes`).

Nothing here is hand-maintained: every definition is read from the rubric. The only code-level
knowledge is how to resolve the rubric's CONDITIONAL applicability entries; the set of CONDITIONAL
entries is asserted against the rubric by tests/test_spec.py so that a rubric change cannot be
silently mis-resolved.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ignosis_eval.contracts.enums import (
    ActionType,
    AttributionBasis,
    AttributionClass,
    Confidence,
    InputMode,
    ModeApplicability,
    OosReason,
    Severity,
    TranscriptProvenance,
)

# (check, input mode) pairs the rubric marks CONDITIONAL, and how they resolve.
CONDITIONAL_RULES: dict[tuple[str, InputMode], str] = {
    ("G7", InputMode.TRANSCRIPT): "header_call_start_ts",
    ("G7", InputMode.AUDIO_TRANSCRIPT): "header_call_start_ts",
    ("PLT-01", InputMode.TRANSCRIPT): "has_timestamps",
    ("PLT-03", InputMode.AUDIO_TRANSCRIPT): "platform_live_asr",
    ("PLT-04", InputMode.AUDIO_TRANSCRIPT): "platform_live_asr",
}


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceElement:
    name: str
    role: str
    absence_allowed: bool = False
    only_for: str | None = None
    sub_rule: str | None = None


@dataclass(frozen=True)
class CheckDef:
    id: str
    kind: str  # "gate" | "code" | "platform"
    dimension: str
    severity: Severity | None
    severity_rule: str | None
    action_types: tuple[ActionType, ...]
    action_type_rule: str | None
    confidence_ceiling: Confidence | None
    confidence_rule: str | None
    sub_rule_ceilings: dict[str, Confidence]
    high_requires: dict[str, str]  # sub-rule ("" for whole check) -> requirement name
    otherwise_ceiling: dict[str, Confidence]
    attribution_class: AttributionClass
    basis: AttributionBasis
    applicable: dict[InputMode, ModeApplicability]
    elements: tuple[EvidenceElement, ...]
    repairable: bool
    borrower_impact: bool
    dw_inducement: bool
    precheck: bool
    methods: tuple[str, ...]
    raw: dict[str, Any] = field(repr=False, compare=False)

    @property
    def is_gate(self) -> bool:
        return self.kind == "gate"


def _conf(v: Any) -> Confidence | None:
    return Confidence(v) if v in {c.value for c in Confidence} else None


def _elements(raw: dict[str, Any]) -> tuple[EvidenceElement, ...]:
    out: list[EvidenceElement] = []
    for e in raw.get("required_evidence_elements", []) or []:
        out.append(EvidenceElement(e["name"], e["role"], bool(e.get("absence_allowed", False)), e.get("only_for")))
    subs = raw.get("sub_rules")
    if isinstance(subs, dict):
        for sub, body in subs.items():
            if not isinstance(body, dict):
                continue
            for e in body.get("required_evidence_elements", []) or []:
                out.append(EvidenceElement(e["name"], e["role"], bool(e.get("absence_allowed", False)),
                                           e.get("only_for"), sub_rule=sub))
    return tuple(out)


def _build(raw: dict[str, Any], kind: str, default_modes: dict[str, str]) -> CheckDef:
    cid = raw["id"]
    modes_raw = raw.get("applicable_modes") or default_modes
    applicable = {InputMode(k): ModeApplicability(v) for k, v in modes_raw.items()}
    at = raw.get("action_type")
    if isinstance(at, list):
        action_types = tuple(ActionType(a) for a in at)
    elif isinstance(at, str):
        action_types = (ActionType(at),)
    else:
        action_types = ()
    severity = raw.get("severity") or raw.get("severity_default")
    subs = raw.get("sub_rules")
    sub_ceilings: dict[str, Confidence] = {}
    high_requires: dict[str, str] = {}
    otherwise: dict[str, Confidence] = {}
    if isinstance(subs, dict):
        for sub, body in subs.items():
            if not isinstance(body, dict):
                continue
            if _conf(body.get("confidence_ceiling")):
                sub_ceilings[sub] = Confidence(body["confidence_ceiling"])
            if body.get("high_requires"):
                high_requires[sub] = str(body["high_requires"])
            if _conf(body.get("otherwise_ceiling")):
                otherwise[sub] = Confidence(body["otherwise_ceiling"])
    if raw.get("high_requires"):
        high_requires[""] = str(raw["high_requires"])
    if _conf(raw.get("otherwise_ceiling")):
        otherwise[""] = Confidence(raw["otherwise_ceiling"])
    return CheckDef(
        id=cid, kind=kind, dimension=str(raw.get("dimension", "PLATFORM" if kind == "platform" else "")),
        severity=Severity(severity) if severity in {s.value for s in Severity} else None,
        severity_rule=raw.get("severity_rule"),
        action_types=action_types, action_type_rule=raw.get("action_type_rule"),
        confidence_ceiling=_conf(raw.get("confidence_ceiling")),
        confidence_rule=raw.get("confidence_ceiling_rule"),
        sub_rule_ceilings=sub_ceilings, high_requires=high_requires, otherwise_ceiling=otherwise,
        attribution_class=AttributionClass(raw["attribution_class"]),
        basis=AttributionBasis(raw["basis"]) if raw.get("basis") else AttributionBasis.RUBRIC,
        applicable=applicable, elements=_elements(raw),
        repairable=bool(raw.get("repairable", False)),
        borrower_impact=bool(raw.get("borrower_impact", False)),
        dw_inducement=bool(raw.get("dw_inducement", False)),
        precheck=bool(raw.get("precheck", False)),
        methods=tuple(raw.get("method", []) or []),
        raw=raw,
    )


def outcome_positive(dispositions: Iterable[str], firmness: str | None, positive_set: Iterable[str],
                     ptp_firmness: Iterable[str]) -> bool:
    """rubric.yaml › outcome_model (AJ-09): a disposition in positive_set makes the outcome positive, except that
    PTP_STATED counts only when the commitment firmness is in positive_ptp_requires_firmness ([firm]), for the full
    or a partial amount. The single implementation used by the engine (tags), gold derivation and card checks."""
    pos, firm = set(positive_set), set(ptp_firmness)
    return any(d in pos and (d != "PTP_STATED" or firmness in firm) for d in dispositions)


@dataclass(frozen=True)
class Registry:
    checks: dict[str, CheckDef]
    gate_ids: tuple[str, ...]
    code_ids: tuple[str, ...]
    platform_ids: tuple[str, ...]
    oos_codes: tuple[str, ...]
    not_evaluated: tuple[str, ...]
    precheck_codes: tuple[str, ...]
    borrower_impact_majors: tuple[str, ...]
    dw_inducement_set: tuple[str, ...]
    positive_set: tuple[str, ...]
    positive_ptp_requires_firmness: tuple[str, ...]
    repair_allowlist: tuple[str, ...]  # AJ-08: {ACC-05}

    @classmethod
    def from_rubric(cls, rubric: dict[str, Any]) -> "Registry":
        default_modes = rubric["default_applicable_modes"]
        checks: dict[str, CheckDef] = {}
        for g in rubric["gates"]:
            checks[g["id"]] = _build(g, "gate", default_modes)
        for c in rubric["codes"]:
            checks[c["id"]] = _build(c, "code", default_modes)
        for p in rubric["platform_signals"]:
            checks[p["id"]] = _build(p, "platform", default_modes)
        for (cid, mode), _ in CONDITIONAL_RULES.items():
            if checks[cid].applicable.get(mode) is not ModeApplicability.CONDITIONAL:
                raise RegistryError(f"rubric no longer marks {cid}/{mode} CONDITIONAL; reconcile CONDITIONAL_RULES")
        for cid, cd in checks.items():
            for mode, app in cd.applicable.items():
                if app is ModeApplicability.CONDITIONAL and (cid, mode) not in CONDITIONAL_RULES:
                    raise RegistryError(f"unrecognised CONDITIONAL applicability {cid}/{mode} (fail closed)")
        ns = rubric["named_sets"]
        om = rubric["outcome_model"]
        allowlist = tuple(rubric["repair_rules"]["allowlist"])
        flagged = {cid for cid, cd in checks.items() if cd.repairable}
        if set(allowlist) != flagged or any(checks[c].is_gate for c in allowlist):
            raise RegistryError(f"repair_rules.allowlist {allowlist} disagrees with `repairable: true` codes "
                                f"{sorted(flagged)} (AJ-08; fail closed)")
        return cls(
            checks=checks,
            gate_ids=tuple(g["id"] for g in rubric["gates"]),
            code_ids=tuple(c["id"] for c in rubric["codes"]),
            platform_ids=tuple(p["id"] for p in rubric["platform_signals"]),
            oos_codes=tuple(o["id"] for o in rubric["out_of_scope_codes"]),
            not_evaluated=tuple(rubric["not_evaluated_in_mvp"]),
            precheck_codes=tuple(rubric["precheck_codes"]),
            borrower_impact_majors=tuple(ns["borrower_impact_majors"]),
            dw_inducement_set=tuple(ns["dw_inducement_set"]),
            positive_set=tuple(om["positive_set"]),
            positive_ptp_requires_firmness=tuple(om["positive_ptp_requires_firmness"]),
            repair_allowlist=allowlist,
        )

    def outcome_positive(self, dispositions: Iterable[str], firmness: str | None) -> bool:
        return outcome_positive(dispositions, firmness, self.positive_set, self.positive_ptp_requires_firmness)

    # ------------------------------------------------------------------ lookups
    def get(self, check_id: str) -> CheckDef:
        try:
            return self.checks[check_id]
        except KeyError as exc:
            raise RegistryError(f"unknown check id {check_id!r}") from exc

    def is_mvp_check(self, check_id: str) -> bool:
        return check_id in self.checks

    @property
    def mvp_code_ids(self) -> tuple[str, ...]:
        """Non-gate MVP codes (dimension codes incl. POL-01) plus platform signals."""
        return self.code_ids + self.platform_ids

    @property
    def major_codes(self) -> tuple[str, ...]:
        """Non-gate codes listed as Major in frozen-contract §4.1 (COM-03 via its PTP variant, POL-01 default)."""
        return tuple(c for c in self.code_ids
                     if self.checks[c].severity is Severity.MAJOR or self.checks[c].severity_rule)

    @property
    def minor_codes(self) -> tuple[str, ...]:
        """Non-gate codes listed as Minor in §4.1 (COM-03 via its callback variant)."""
        return tuple(c for c in self.code_ids
                     if self.checks[c].severity is Severity.MINOR or c == "COM-03")

    # ------------------------------------------------------------------ capability table
    def mode_status(self, check_id: str, input_mode: InputMode, *, has_call_start_ts: bool, has_timestamps: bool,
                    provenance: TranscriptProvenance | None) -> tuple[ModeApplicability, OosReason | None]:
        """Resolve applicability to EVALUABLE or OUT_OF_SCOPE (+ reason) for one unit."""
        cd = self.get(check_id)
        app = cd.applicable[input_mode]
        if app is ModeApplicability.EVALUABLE:
            return ModeApplicability.EVALUABLE, None
        if app is ModeApplicability.OUT_OF_SCOPE:
            return ModeApplicability.OUT_OF_SCOPE, OosReason.MODE_CAPABILITY
        rule = CONDITIONAL_RULES[(check_id, input_mode)]
        if rule == "header_call_start_ts":
            ok, reason = has_call_start_ts, OosReason.EXTERNAL_DATA_REQUIRED  # rubric G7 when_header_absent
        elif rule == "has_timestamps":
            ok, reason = has_timestamps, OosReason.MODE_CAPABILITY
        elif rule == "platform_live_asr":
            ok, reason = provenance is TranscriptProvenance.PLATFORM_LIVE_ASR, OosReason.MODE_CAPABILITY
        else:  # pragma: no cover - guarded in from_rubric
            raise RegistryError(rule)
        return (ModeApplicability.EVALUABLE, None) if ok else (ModeApplicability.OUT_OF_SCOPE, reason)
