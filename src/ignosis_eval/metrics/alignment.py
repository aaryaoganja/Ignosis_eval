"""Align gold labels with evaluator outputs into ItemRep units.

The only evaluator-side inputs are the declared artifacts of a run: the Evaluation Record JSON and the
normalized input JSON per item/repetition. Schema-invalid or missing records are kept as ItemReps with
an integrity violation (they are never silently dropped).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ignosis_eval.contracts.benchmark import CaseMetadata
from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.enums import ABSTENTION_VERDICTS, InputMode, Verdict
from ignosis_eval.contracts.evaluation_record import EvaluationRecord
from ignosis_eval.contracts.gold_label import GoldLabel
from ignosis_eval.contracts.profile import Profile
from ignosis_eval.contracts.record_checks import (
    IntegrityCode,
    Violation,
    check_modality_conformance,
    check_record_integrity,
)


@dataclass
class RawOutput:
    """Evaluator-side artifacts for one item/repetition, as read from run storage."""

    record_json: Any | None = None
    normalized_input_json: Any | None = None
    errors: list[dict] = field(default_factory=list)


@dataclass
class ItemRep:
    item_id: str
    rep: int
    case: CaseMetadata
    gold: GoldLabel
    record: EvaluationRecord | None
    normalized_input: CanonicalInput | None
    integrity: list[Violation] = field(default_factory=list)
    modality: list[Violation] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict | None:
        return self.record.verdict if self.record else None

    @property
    def abstained(self) -> bool:
        return self.record is not None and self.record.verdict in ABSTENTION_VERDICTS

    @property
    def input_mode(self) -> InputMode:
        return self.normalized_input.input_mode if self.normalized_input else self.case.intended_modality

    @property
    def language(self) -> str:
        return self.case.language


def align(
    item_id: str,
    rep: int,
    case: CaseMetadata,
    gold: GoldLabel,
    raw: RawOutput,
    profile: Profile | None,
) -> ItemRep:
    integrity: list[Violation] = []
    record: EvaluationRecord | None = None
    inp: CanonicalInput | None = None
    if raw.normalized_input_json is not None:
        try:
            inp = CanonicalInput.model_validate(raw.normalized_input_json)
        except ValidationError as exc:
            integrity.append(Violation("normalized_input_invalid", _short(exc)))
    if raw.record_json is None:
        integrity.append(Violation(IntegrityCode.MISSING_RECORD, _error_summary(raw.errors)))
    else:
        try:
            record = EvaluationRecord.model_validate(raw.record_json)
        except ValidationError as exc:
            integrity.append(Violation(IntegrityCode.SCHEMA_INVALID, _short(exc)))
    modality: list[Violation] = []
    if record is not None:
        integrity += check_record_integrity(record, inp, profile)
        if inp is not None:
            modality = check_modality_conformance(record, inp, profile)
    return ItemRep(item_id, rep, case, gold, record, inp, integrity, modality, list(raw.errors))


def _short(exc: ValidationError) -> str:
    errs = exc.errors()
    first = errs[0] if errs else {}
    loc = ".".join(str(x) for x in first.get("loc", ()))
    return f"{len(errs)} error(s); first at {loc or '<root>'}: {first.get('msg', '')}"


def _error_summary(errors: list[dict]) -> str:
    if not errors:
        return "no record written"
    e = errors[-1]
    return json.dumps({k: e.get(k) for k in ("stage", "type", "message")}, ensure_ascii=False)[:300]
