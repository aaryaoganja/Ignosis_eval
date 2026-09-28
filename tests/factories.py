"""Hand-built, obviously synthetic test stubs (P-1 rule 3). Nothing here is benchmark content or gold:
turn texts are placeholders like "stub agent line alpha", item ids start with ZZ-, and lexicon terms in the
test-only profile are nonsense tokens (zz...). Real bench-a1 content is authored by humans (B-01/B-02).
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ignosis_eval.benchmark.checks import check_bench
from ignosis_eval.benchmark.layout import BenchLayout
from ignosis_eval.contracts.enums import (
    ActionType,
    Attribution,
    AttributionBasis,
    Confidence,
    EvaluabilityStatus,
    GateStatus,
    RecordStatus,
    Role,
    Severity,
    UnitMode,
)
from ignosis_eval.contracts.evaluation_record import (
    AttributionResult,
    EvaluabilityResult,
    EvaluationRecord,
    Evidence,
    FailureInfo,
    Finding,
    GateResult,
    SystemInfo,
    Tags,
)
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.integrity.freeze import build_bench_manifest, freeze_gold
from ignosis_eval.pipeline.normalize import unit_facts
from ignosis_eval.spec.loader import Spec, load_spec

REPO = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO / "docs" / "spec"
GATES = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9")
STUB_TURNS = (("AGENT", "stub agent line alpha"), ("BORROWER", "stub borrower line beta"),
              ("AGENT", "stub agent line gamma"), ("BORROWER", "stub borrower line delta"),
              ("AGENT", "stub agent line epsilon"))
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@lru_cache(maxsize=1)
def spec() -> Spec:
    return load_spec(SPEC_DIR)


def test_profile(tmp: Path, *, terms: dict[tuple[str, ...], list[str]] | None = None,
                 thresholds: dict[str, Any] | None = None, name: str = "test_profile.yaml") -> Path:
    """TEST-ONLY copy of the spec profile with synthetic placeholder values. Non-canonical: a locked run refuses it."""
    prof = yaml.safe_load((SPEC_DIR / "profile.yaml").read_text(encoding="utf-8"))
    for path, value in (terms or {}).items():
        node = prof["lexicons"]
        for key in path:
            node = node[key]
        node["terms"] = list(value)
    for k, v in (thresholds or {}).items():
        prof["thresholds"][k]["value"] = v
    p = tmp / name
    p.write_text(yaml.safe_dump(prof, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


def stub_transcript(turns=STUB_TURNS, *, header: dict[str, Any] | None = None, timestamps: bool = False) -> str:
    lines = [f"# {k}: {v}" for k, v in (header or {}).items()]
    for i, (role, text) in enumerate(turns):
        ts = f"[00:{2 * i:02d}.0-00:{2 * i + 1:02d}.5] " if timestamps else ""
        lines.append(f"{ts}{role}: {text}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------ records
def gate(g: str, status: str = "PASS", **kw: Any) -> GateResult:
    if status == "FAIL":
        kw.setdefault("critical_status", "SUSPECTED")
    return GateResult(gate=g, status=GateStatus(status), **kw)


def ev(turn: int, quote: str, role: str = "AGENT", **kw: Any) -> Evidence:
    return Evidence(turn=turn, quote=quote, role=Role(role), **kw)


def finding(code: str, turns: list[int], *, severity: str = "MAJOR", state: str = "ASSERTED",
            attribution: str = "AGENT_BEHAVIOR", quote: str = "stub agent line alpha", role: str = "AGENT",
            **kw: Any) -> Finding:
    return Finding(code=code, severity=Severity(severity), finding_state=state, confidence=Confidence.MEDIUM,
                   action_type=ActionType.REMEDIATE,
                   attribution=AttributionResult(primary=Attribution(attribution), basis=AttributionBasis.RUBRIC),
                   evidence=[ev(t, quote, role) for t in turns], **kw)


def record(gates: dict[str, Any] | None = None, findings: list[Finding] | None = None, *, verdict: str | None = None,
           critical_status: str | None = None, unit_mode: str = "TRANSCRIPT", system: str = "SYS-1", **kw: Any
           ) -> EvaluationRecord:
    """An OK record. `gates` maps gate -> status string or a GateResult; unspecified gates are PASS."""
    gs = []
    for g in GATES:
        v = (gates or {}).get(g, "PASS")
        gs.append(v if isinstance(v, GateResult) else gate(g, v))
    fired = [x for x in gs if x.status is GateStatus.FAIL]
    if verdict is None:
        verdict = "CRITICAL_FAIL" if fired else "MEETS_BAR"
    if verdict == "CRITICAL_FAIL" and critical_status is None:
        critical_status = "CONFIRMED" if any(x.critical_status and x.critical_status.value == "CONFIRMED"
                                             for x in fired) else "SUSPECTED"
    input_mode = {"TRANSCRIPT": "TRANSCRIPT", "T-gold": "TRANSCRIPT", "T-asr": "TRANSCRIPT", "A": "AUDIO",
                  "A+T": "AUDIO_TRANSCRIPT", "A+T-platform": "AUDIO_TRANSCRIPT"}[unit_mode]
    return EvaluationRecord(
        record_status=RecordStatus.OK, confidence_source=kw.pop("confidence_source", "COMPUTED"),
        system=SystemInfo(system=system, version="test"),
        contract_version="1.1.0-frozen", rubric_version="1.1-mvp", profile_id="collections_default_v1",
        profile_version="1.1.0", input_mode=input_mode, unit_mode=unit_mode, verdict=verdict,
        critical_status=critical_status, within_scope_complete=kw.pop("within_scope_complete", True),
        evaluability=EvaluabilityResult(status=EvaluabilityStatus.EVALUABLE), gates=gs, findings=findings or [],
        tags=kw.pop("tags", Tags()), **kw)


def failed(unit_mode: str = "TRANSCRIPT", system: str = "SYS-1") -> EvaluationRecord:
    input_mode = "TRANSCRIPT" if unit_mode in ("TRANSCRIPT", "T-gold", "T-asr") else "AUDIO"
    return EvaluationRecord(record_status=RecordStatus.EVALUATION_FAILED, system=SystemInfo(system=system, version="t"),
                            contract_version="1.1.0-frozen", rubric_version="1.1-mvp",
                            profile_id="collections_default_v1", profile_version="1.1.0", input_mode=input_mode,
                            unit_mode=unit_mode, failure=FailureInfo(reason="schema-invalid (stub)", schema_attempts=2))


def make_ni(sp: Spec, turns=STUB_TURNS, *, header: dict[str, Any] | None = None, evaluability: str | None = None,
            reasons: tuple[str, ...] = ()):
    """A TRANSCRIPT NormalizedInput built by the real front end from synthetic stub turns."""
    from ignosis_eval.contracts.canonical_input import FrontendResult, NormalizedInput, TranscriptHeader, Turn
    from ignosis_eval.contracts.enums import InputMode
    from ignosis_eval.pipeline.prechecks import run_frontend

    ts = [Turn(turn=i, role=Role(r), text=t, supplied_text=t, unreliable=Role(r) is Role.UNKNOWN)
          for i, (r, t) in enumerate(turns, 1)]
    h = TranscriptHeader(**(header or {}))
    fr = run_frontend(ts, h, InputMode.TRANSCRIPT, sp)
    if evaluability is not None:
        fr = FrontendResult(evaluability_status=EvaluabilityStatus(evaluability), reason_codes=list(reasons),
                            prechecks=fr.prechecks, precheck_findings=fr.precheck_findings, steps=fr.steps)
    return NormalizedInput(input_mode="TRANSCRIPT", unit_mode="TRANSCRIPT", header=h, has_timestamps=False, turns=ts,
                           role_mapping_confidence=1.0, frontend=fr)


def body_json(rec: EvaluationRecord) -> str:
    """The RecordBody part of a record, as an LLM would emit it (for replay fixtures)."""
    return json.dumps(rec.model_dump(mode="json", include={
        "verdict", "critical_status", "within_scope_complete", "evaluability", "gates", "findings", "checks",
        "dimensions", "outcome", "tags", "routing_tier", "out_of_scope", "unverified_agent_commitments"}))


def write_replay(replay_dir: Path, task: str, ni, entries: list[Any], by_rep: dict[int, list[Any]] | None = None) -> None:
    from ignosis_eval.evaluators.base import input_sha256

    d = replay_dir / task
    d.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"default": entries}
    if by_rep:
        data["by_rep"] = {str(k): v for k, v in by_rep.items()}
    (d / f"{input_sha256(ni)}.json").write_text(json.dumps(data), encoding="utf-8")


# ------------------------------------------------------------------------------------------ gold
def gold(item_id: str = "ZZ-T01", *, split: str = "dev", gates: dict[str, dict[str, Any] | str] | None = None,
         findings: list[dict[str, Any]] | None = None, verdict: str | None = None, wsc: bool = True,
         **kw: Any) -> GoldLabel:
    gs: dict[str, Any] = {}
    for g in GATES:
        v = (gates or {}).get(g, "PASS")
        gs[g] = {"status": v} if isinstance(v, str) else v
    if verdict is None:
        verdict = "CRITICAL_FAIL" if any(v["status"] == "FAIL" for v in gs.values()) else "MEETS_BAR"
    data = {"item_id": item_id, "split": split, "gates": gs, "findings": findings or [],
            "verdict": {"value": verdict, "within_scope_complete": wsc},
            "provenance": {"labeling_protocol_version": "test-protocol", "created_at": NOW.isoformat()},
            "labelers": [{"labeler_id": "lab-x", "role": "labeler_x", "labeled_at": NOW.isoformat(),
                          "saw_evaluator_outputs": False}], **kw}
    return GoldLabel.model_validate(data)


# ------------------------------------------------------------------------------------------ bench
def card(item_id: str, *, split: str = "dev", pack: str = "core", unit_modes: list[str] | None = None,
         **kw: Any) -> dict[str, Any]:
    c = {"case_card_version": "case_card/2.1.0", "item_id": item_id, "status": "approved",
         "authoring": {"author_id": "auth-1", "created_on": str(date(2026, 1, 1)), "reviewers": ["rev-1"]},
         "split": split, "pack": pack, "language": "en", "unit_modes": unit_modes or ["TRANSCRIPT"],
         "intent": "synthetic stub item for automated tests", "rationale":
         "Synthetic stub card used only by automated tests; it is not benchmark content.",
         "scenario": "stub", "target_behavior": "stub", "target_check": None, "severity": None,
         "repair_status": None, "ambiguity_notes": "none (synthetic stub)"}
    c.update(kw)
    return c


def add_item(layout: BenchLayout, item_id: str, *, split: str = "dev", pack: str = "core",
             transcript: str | None = None, unit_modes: list[str] | None = None, gold_label: GoldLabel | None = None,
             card_overrides: dict[str, Any] | None = None, audio: bytes | None = None,
             platform_transcript: str | None = None) -> Path:
    from ignosis_eval.benchmark.layout import scope_of
    from ignosis_eval.contracts.enums import Split

    sp = Split(split)
    ip = layout.item_paths(sp, item_id)
    ip.item_dir.mkdir(parents=True)
    modes = unit_modes or ["TRANSCRIPT"]
    arts: dict[str, Any] = {}
    if transcript is not False:
        (ip.item_dir / "transcript.txt").write_text(transcript or stub_transcript(), encoding="utf-8")
        arts["transcript"] = "transcript.txt"
    if audio is not None:
        (ip.item_dir / "audio.wav").write_bytes(audio)
        arts["audio"] = "audio.wav"
    if platform_transcript is not None:
        (ip.item_dir / "platform.txt").write_text(platform_transcript, encoding="utf-8")
        arts["platform_transcript"] = "platform.txt"
    meta = {"item_id": item_id, "split": split, "pack": pack, "language": "en", "unit_modes": modes,
            "artifacts": arts, "synthetic": True}
    ip.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    scope = scope_of(sp)
    layout.cards_dir(scope).mkdir(parents=True, exist_ok=True)
    ip.card_path.write_text(yaml.safe_dump(card(item_id, split=split, pack=pack, unit_modes=modes,
                                                **(card_overrides or {})), sort_keys=False), encoding="utf-8")
    layout.gold_dir(scope).mkdir(parents=True, exist_ok=True)
    g = gold_label or gold(item_id, split=split)
    ip.gold_path.write_text(json.dumps(g.model_dump(mode="json"), indent=2), encoding="utf-8")
    return ip.item_dir


def write_registries(layout: BenchLayout, data: dict[str, Any] | None = None) -> None:
    layout.root.mkdir(parents=True, exist_ok=True)
    body = {"schema_version": "registries/1.0.0", "controls": [], "pairs": [], "twins": []}
    body.update(copy.deepcopy(data or {}))
    layout.registries_path.write_text(json.dumps(body, indent=2), encoding="utf-8")


def freeze(layout: BenchLayout, sp: Spec, scope: str = "dev", version: str = "t1") -> None:
    def validator(require_gold: bool):
        return lambda: [str(i) for i in check_bench(layout, sp, scopes=(scope,), require_gold=require_gold).errors]

    build_bench_manifest(layout, scope, dataset_name="bench-a1-test-stub", dataset_version=version,
                         created_by="tests", facts_fn=unit_facts, validator=validator(False))
    freeze_gold(layout, scope, gold_version=version, frozen_by="tests", labeling_protocol_version="test-protocol",
                validator=validator(True))


__all__ = ["GATES", "STUB_TURNS", "UnitMode", "add_item", "card", "ev", "failed", "finding", "freeze", "gate", "gold",
           "record", "spec", "stub_transcript", "test_profile", "write_registries"]
