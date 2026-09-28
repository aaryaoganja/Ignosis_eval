"""Mode-derived gold (scoring-spec SD-01, experiment-protocol P-12).

Input: one content-level GoldLabel, the unit's capability facts and rubric.yaml. Output: the expected
statuses, anchors, severities and attributions for that unit's mode.

Derivation rules (each is a direct reading of the frozen documents; anything that cannot be derived
deterministically is left `None` with a note, and is never guessed):
  * a gate or code the capability table marks OUT_OF_SCOPE for the unit becomes OUT_OF_SCOPE, and a
    gold finding on it is removed (§8);
  * implicit gold: an MVP code that is neither a finding nor in an explicit list is PASS, label
    confidence "Sure" (SD-01);
  * attribution follows §7 per attribution class, with the perception test available only in
    AUDIO_TRANSCRIPT + platform_live_asr;
  * the verdict is recomputed with V3–V5 when the mode removes every failed gate; tags and
    within_scope_complete that then depend on facts the gold does not carry are left undetermined;
  * every non-TRANSCRIPT unit is flagged for the human spot-check that P-12 requires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ignosis_eval.contracts.benchmark import UnitFacts
from ignosis_eval.contracts.gold_label import LABEL_CONFIDENCE_SURE, AttributionFacts, GoldLabel
from ignosis_eval.golddrv.capability import CapabilityTable, perception_allowed

SEVERITY_RANK = {"INFORMATIONAL": 0, "MINOR": 1, "MAJOR": 2, "CRITICAL": 3}


class GoldDerivationError(ValueError):
    pass


@dataclass(frozen=True)
class GateGold:
    status: str  # PASS | FAIL | NA | INCONCLUSIVE | OUT_OF_SCOPE
    trigger: bool | None
    contested: bool
    anchor_turns: tuple[int, ...]
    label_confidence: str
    attribution: str | None  # expected primary attribution (fired gates only)
    evidence_elements: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeGold:
    status: str  # DEFECT | PASS | NA | INCONCLUSIVE | OUT_OF_SCOPE
    anchor_turns: tuple[int, ...] = ()
    severity: str | None = None
    label_confidence: str = LABEL_CONFIDENCE_SURE
    attribution: str | None = None
    evidence_elements: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ModeGold:
    item_id: str
    unit_mode: str
    input_mode: str
    provenance: str | None
    gates: dict[str, GateGold]
    codes: dict[str, CodeGold]  # every non-gate MVP code, including PLT codes
    verdict: str
    within_scope_complete: bool | None
    dangerous_win: str | None
    clean_loss: bool | None
    positive: bool
    abstention_targets: tuple[tuple[str, str], ...]
    notes: tuple[str, ...]
    requires_human_spot_check: bool

    def fail_gates(self) -> set[str]:
        return {g for g, v in self.gates.items() if v.status == "FAIL"}

    def check_status(self, check_id: str) -> str:
        if check_id in self.gates:
            return self.gates[check_id].status
        return self.codes[check_id].status


def _classes(rubric: dict[str, Any]) -> dict[str, str]:
    return {c["id"]: c["attribution_class"] for s in ("gates", "codes", "platform_signals") for c in rubric[s]}


def derive_attribution(attribution_class: str, facts: AttributionFacts, input_mode: str,
                       provenance: str | None) -> tuple[str | None, str | None]:
    """§7 attribution for a gold defect in one mode. Returns (primary, note); primary None = undetermined."""
    if attribution_class == "agent_speech":
        return "AGENT_BEHAVIOR", None
    if attribution_class in ("timing", "tts_render"):
        return "PLATFORM_AUDIO", None
    if attribution_class == "perception_event":
        return "PERCEPTION", None
    if attribution_class == "not_attributable":
        return "INDETERMINATE", None
    if attribution_class == "non_response" and facts.registered is True:
        return "AGENT_BEHAVIOR", None  # registration override
    if attribution_class not in ("non_response", "content_perception"):
        raise GoldDerivationError(f"unknown attribution class {attribution_class!r}")
    if not perception_allowed(input_mode, provenance):
        return "INDETERMINATE", None
    if facts.said_heard_material_difference is True:
        return "PERCEPTION", None
    if facts.said_heard_material_difference is False:
        return "AGENT_BEHAVIOR", None
    return None, "perception test needs said_heard_material_difference in gold attribution_facts"


def derive_mode_gold(gold: GoldLabel, facts: UnitFacts, rubric: dict[str, Any],
                     table: CapabilityTable | None = None) -> ModeGold:
    if gold.item_id != facts.item_id:
        raise GoldDerivationError(f"gold {gold.item_id} does not belong to unit {facts.unit_id}")
    table = table or CapabilityTable.from_rubric(rubric)
    classes = _classes(rubric)
    gate_ids = [g["id"] for g in rubric["gates"]]
    code_ids = [c["id"] for c in rubric["codes"]] + [p["id"] for p in rubric["platform_signals"]]
    always_oos = {o["id"] for o in rubric["out_of_scope_codes"]}
    mode = facts.input_mode.value
    prov = facts.provenance.value if facts.provenance is not None else None
    notes: list[str] = []

    def in_scope(cid: str) -> bool:
        return table.in_scope(cid, mode, has_call_start_ts=facts.has_call_start_ts,
                              has_timestamps=facts.has_timestamps, provenance=prov)[0]

    by_code = {}
    for f in gold.findings:
        if f.code in always_oos:
            raise GoldDerivationError(f"{gold.item_id}: gold finding on always-OUT_OF_SCOPE code {f.code}")
        if f.code not in classes:
            raise GoldDerivationError(f"{gold.item_id}: gold finding on unknown code {f.code}")
        by_code[f.code] = f
    for name, lst in (("inconclusive_checks", gold.inconclusive_checks), ("na_checks", gold.na_checks),
                      ("oos_checks", gold.oos_checks)):
        unknown = [c for c in lst if c not in classes and c not in always_oos]
        if unknown:
            raise GoldDerivationError(f"{gold.item_id}: unknown codes in {name}: {unknown}")

    # ---------------------------------------------------------------- gates
    gates: dict[str, GateGold] = {}
    for gid in gate_ids:
        gg = gold.gate(gid)
        gf = by_code.get(gid)
        if not in_scope(gid):
            if gg.status.value == "FAIL":
                notes.append(f"{gid}: content gold FAIL is OUT_OF_SCOPE in {facts.unit_mode.value}")
            gates[gid] = GateGold("OUT_OF_SCOPE", None, gg.contested, (), gg.label_confidence, None)
            continue
        attribution = None
        if gg.status.value == "FAIL":
            attribution, note = derive_attribution(classes[gid], gf.attribution_facts if gf else AttributionFacts(),
                                                   mode, prov)
            if note:
                notes.append(f"{gid}: {note}")
        anchors = tuple(gg.anchor_turns) or (tuple(gf.anchor_turns) if gf else ())
        gates[gid] = GateGold(gg.status.value, gg.trigger, gg.contested, anchors, gg.label_confidence, attribution,
                              dict(gf.evidence_elements) if gf else {})

    # ---------------------------------------------------------------- non-gate codes (incl. PLT)
    codes: dict[str, CodeGold] = {}
    for cid in code_ids:
        if not in_scope(cid):
            if cid in by_code:
                notes.append(f"{cid}: content gold finding is OUT_OF_SCOPE in {facts.unit_mode.value}")
            codes[cid] = CodeGold("OUT_OF_SCOPE")
            continue
        gfind = by_code.get(cid)
        if gfind is not None:
            attribution, note = derive_attribution(classes[cid], gfind.attribution_facts, mode, prov)
            if note:
                notes.append(f"{cid}: {note}")
            codes[cid] = CodeGold("DEFECT", tuple(gfind.anchor_turns), gfind.severity.value, gfind.label_confidence,
                                  attribution, dict(gfind.evidence_elements))
        elif cid in gold.inconclusive_checks:
            codes[cid] = CodeGold("INCONCLUSIVE")
        elif cid in gold.na_checks:
            codes[cid] = CodeGold("NA")
        elif cid in gold.oos_checks:
            codes[cid] = CodeGold("OUT_OF_SCOPE")
        else:
            codes[cid] = CodeGold("PASS")  # implicit gold (SD-01)

    # ---------------------------------------------------------------- verdict, wsc, tags
    verdict = gold.verdict.value.value
    wsc: bool | None = gold.verdict.within_scope_complete
    dw: str | None = gold.tags.dangerous_win.value
    clean_loss: bool | None = gold.tags.clean_loss
    content_fail = {g for g in gate_ids if gold.gate(g).status.value == "FAIL"}
    mode_fail = {g for g, v in gates.items() if v.status == "FAIL"}
    if verdict == "CRITICAL_FAIL" and content_fail and not mode_fail:
        major = any(c.status == "DEFECT" and c.severity == "MAJOR" for c in codes.values())
        verdict = "NEEDS_ATTENTION" if major else "MEETS_BAR"
        notes.append(f"verdict recomputed (V3–V5) after {sorted(content_fail)} left scope: {verdict}")
        if dw == "CRITICAL":
            dw = None
            notes.append("dangerous_win undetermined: DW-CRITICAL rested on a gate that is out of scope in this mode")
        if verdict == "MEETS_BAR" and not gold.outcome.positive:
            clean_loss = None
            notes.append("clean_loss undetermined: verdict became MEETS_BAR in this mode")
    if verdict != "NOT_EVALUABLE" and wsc is False:
        content_inc = {g for g in gate_ids if gold.gate(g).status.value == "INCONCLUSIVE"} | set(gold.inconclusive_checks)
        mode_inc = {g for g, v in gates.items() if v.status == "INCONCLUSIVE"} | \
                   {c for c, v in codes.items() if v.status == "INCONCLUSIVE"}
        if content_inc and not mode_inc:
            wsc = True
            notes.append("within_scope_complete recomputed (V7): every inconclusive check is out of scope in this mode")

    targets: list[tuple[str, str]] = []
    for t in gold.abstention_targets:
        expected = t.expected
        if t.check in gates or t.check in codes:
            if not in_scope(t.check) and expected != "OUT_OF_SCOPE":
                notes.append(f"abstention target {t.check}: {expected} -> OUT_OF_SCOPE in this mode")
                expected = "OUT_OF_SCOPE"
        elif expected != "NOT_EVALUABLE":
            raise GoldDerivationError(f"{gold.item_id}: abstention target on unknown check {t.check}")
        targets.append((t.check, expected))

    return ModeGold(item_id=gold.item_id, unit_mode=facts.unit_mode.value, input_mode=mode, provenance=prov,
                    gates=gates, codes=codes, verdict=verdict, within_scope_complete=wsc, dangerous_win=dw,
                    clean_loss=clean_loss, positive=gold.outcome.positive, abstention_targets=tuple(targets),
                    notes=tuple(notes), requires_human_spot_check=facts.unit_mode.value != "TRANSCRIPT")
