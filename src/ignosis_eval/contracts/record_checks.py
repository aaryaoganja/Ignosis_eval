"""Semantic checks on Evaluation Records against the declared contract.

Two families:

* integrity violations  — the record breaks a contract invariant (gate precedence, dangling references,
                          verdict/evaluability mismatch, unknown taxonomy ids, ...). Any one of these
                          makes the item-repetition an *integrity failure*.
* modality violations   — the record asserts something the input modality cannot support (audio evidence
                          on a transcript-only input; a PASS/FAIL on a gate whose required capability is
                          missing). Feeds *modality conformance*.

Both are pure functions of (record, normalized input, profile). They contain no evaluator-specific logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.capabilities import available_capabilities, missing_capabilities
from ignosis_eval.contracts.enums import (
    EVALUABILITY_TO_VERDICTS,
    EvidenceModality,
    GateStatus,
    Verdict,
)
from ignosis_eval.contracts.evaluation_record import EvaluationRecord
from ignosis_eval.contracts.profile import Profile


class IntegrityCode(StrEnum):
    MISSING_RECORD = "missing_record"
    SCHEMA_INVALID = "schema_invalid"
    CALL_ID_MISMATCH = "call_id_mismatch"
    INPUT_MODE_MISMATCH = "input_mode_mismatch"
    PROFILE_MISMATCH = "profile_mismatch"
    VERDICT_EVALUABILITY_MISMATCH = "verdict_evaluability_mismatch"
    GATE_PRECEDENCE_VIOLATION = "gate_precedence_violation"
    DANGLING_EVIDENCE_REF = "dangling_evidence_ref"
    UNKNOWN_TURN_REF = "unknown_turn_ref"
    FINDING_WITHOUT_EVIDENCE = "finding_without_evidence"
    UNKNOWN_GATE_ID = "unknown_gate_id"
    UNKNOWN_DEFECT_ID = "unknown_defect_id"
    UNKNOWN_DIMENSION_ID = "unknown_dimension_id"
    DW_CL_CONTRADICTION = "dangerous_win_and_clean_loss"


class ModalityCode(StrEnum):
    AUDIO_EVIDENCE_WITHOUT_AUDIO = "audio_evidence_without_audio"
    TRANSCRIPT_EVIDENCE_WITHOUT_TRANSCRIPT = "transcript_evidence_without_transcript"
    METADATA_EVIDENCE_UNAVAILABLE = "metadata_evidence_unavailable"
    GATE_ASSERTED_WITHOUT_CAPABILITY = "gate_asserted_without_capability"
    DEFECT_ASSERTED_WITHOUT_CAPABILITY = "defect_asserted_without_capability"
    DIMENSION_SCORED_WITHOUT_CAPABILITY = "dimension_scored_without_capability"


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


def check_record_integrity(
    record: EvaluationRecord,
    normalized_input: CanonicalInput | None = None,
    profile: Profile | None = None,
) -> list[Violation]:
    v: list[Violation] = []
    ev_ids = set(record.evidence_map())

    if record.verdict not in EVALUABILITY_TO_VERDICTS[record.evaluability.status]:
        v.append(Violation(IntegrityCode.VERDICT_EVALUABILITY_MISMATCH,
                           f"verdict={record.verdict} evaluability={record.evaluability.status}"))
    failed = [g.gate_id for g in record.gates if g.status is GateStatus.FAIL]
    if failed and record.verdict is not Verdict.FAIL:
        v.append(Violation(IntegrityCode.GATE_PRECEDENCE_VIOLATION,
                           f"gates {failed} FAIL but verdict={record.verdict}"))

    refs: list[tuple[str, str]] = []
    for g in record.gates:
        refs += [(f"gate {g.gate_id}", e) for e in g.evidence_ids]
    for d in record.dimensions:
        refs += [(f"dimension {d.dimension_id}", e) for e in d.evidence_ids]
    for f in record.findings:
        refs += [(f"finding {f.finding_id}", e) for e in f.evidence_ids]
        refs += [(f"finding {f.finding_id} attribution", e) for e in f.attribution.evidence_ids]
        if not f.evidence_ids:
            v.append(Violation(IntegrityCode.FINDING_WITHOUT_EVIDENCE, f.finding_id))
    if record.primary_attribution:
        refs += [("primary_attribution", e) for e in record.primary_attribution.evidence_ids]
    if record.observable_outcomes:
        refs += [("observable_outcomes", e) for e in record.observable_outcomes.evidence_ids]
    for owner, e in refs:
        if e not in ev_ids:
            v.append(Violation(IntegrityCode.DANGLING_EVIDENCE_REF, f"{owner} -> {e}"))

    if record.dangerous_win and record.clean_loss:
        v.append(Violation(IntegrityCode.DW_CL_CONTRADICTION))

    if normalized_input is not None:
        if record.call_id != normalized_input.call_id:
            v.append(Violation(IntegrityCode.CALL_ID_MISMATCH, f"{record.call_id} != {normalized_input.call_id}"))
        if record.input_mode != normalized_input.input_mode:
            v.append(Violation(IntegrityCode.INPUT_MODE_MISMATCH,
                               f"{record.input_mode} != {normalized_input.input_mode}"))
        turns = normalized_input.turn_map()
        for ev in record.evidence:
            unknown = [t for t in ev.turn_ids if t not in turns]
            if unknown:
                v.append(Violation(IntegrityCode.UNKNOWN_TURN_REF, f"{ev.evidence_id}: {unknown}"))

    if profile is not None:
        if (record.profile_id, record.profile_version) != (profile.profile_id, profile.profile_version):
            v.append(Violation(IntegrityCode.PROFILE_MISMATCH,
                               f"{record.profile_id}@{record.profile_version} != "
                               f"{profile.profile_id}@{profile.profile_version}"))
        for g in record.gates:
            if profile.gate_def(g.gate_id) is None:
                v.append(Violation(IntegrityCode.UNKNOWN_GATE_ID, g.gate_id))
        for d in record.dimensions:
            if profile.dimension_def(d.dimension_id) is None:
                v.append(Violation(IntegrityCode.UNKNOWN_DIMENSION_ID, d.dimension_id))
        for f in record.findings:
            if profile.defect_def(f.defect_id) is None:
                v.append(Violation(IntegrityCode.UNKNOWN_DEFECT_ID, f.defect_id))
            if f.gate_id is not None and profile.gate_def(f.gate_id) is None:
                v.append(Violation(IntegrityCode.UNKNOWN_GATE_ID, f"finding {f.finding_id}: {f.gate_id}"))
    return v


def check_modality_conformance(
    record: EvaluationRecord, normalized_input: CanonicalInput, profile: Profile | None = None
) -> list[Violation]:
    v: list[Violation] = []
    caps = available_capabilities(normalized_input)
    for ev in record.evidence:
        if ev.modality is EvidenceModality.AUDIO and normalized_input.audio is None:
            v.append(Violation(ModalityCode.AUDIO_EVIDENCE_WITHOUT_AUDIO, ev.evidence_id))
        elif ev.modality is EvidenceModality.TRANSCRIPT and normalized_input.transcript is None:
            v.append(Violation(ModalityCode.TRANSCRIPT_EVIDENCE_WITHOUT_TRANSCRIPT, ev.evidence_id))
        elif (
            ev.modality is EvidenceModality.METADATA
            and ev.metadata_field == "call_start_ts"
            and normalized_input.call_start_ts is None
        ):
            v.append(Violation(ModalityCode.METADATA_EVIDENCE_UNAVAILABLE, ev.evidence_id))
    if profile is None:
        return v
    for g in record.gates:
        gd = profile.gate_def(g.gate_id)
        if gd and g.status in (GateStatus.PASS, GateStatus.FAIL):
            miss = missing_capabilities(gd.requires, caps)
            if miss:
                v.append(Violation(ModalityCode.GATE_ASSERTED_WITHOUT_CAPABILITY,
                                   f"{g.gate_id}={g.status} lacks {[m.value for m in miss]}"))
    for f in record.findings:
        dd = profile.defect_def(f.defect_id)
        if dd:
            miss = missing_capabilities(dd.requires, caps)
            if miss:
                v.append(Violation(ModalityCode.DEFECT_ASSERTED_WITHOUT_CAPABILITY,
                                   f"{f.defect_id} lacks {[m.value for m in miss]}"))
    for d in record.dimensions:
        dd = profile.dimension_def(d.dimension_id)
        if dd and d.evaluable:
            miss = missing_capabilities(dd.requires, caps)
            if miss:
                v.append(Violation(ModalityCode.DIMENSION_SCORED_WITHOUT_CAPABILITY,
                                   f"{d.dimension_id} lacks {[m.value for m in miss]}"))
    return v
