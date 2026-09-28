"""Record assembly helpers shared by the systems: parse a structured body, build OK / EVALUATION_FAILED records."""

from __future__ import annotations

import json

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import RecordStatus
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, FailureInfo, RecordBody, SystemInfo
from ignosis_eval.contracts.record_checks import schema_errors
from ignosis_eval.evaluators.llm import OutputSchemaError
from ignosis_eval.spec.loader import Spec


def _meta(ni: NormalizedInput, spec: Spec, system: SystemInfo) -> dict:
    return {"system": system, "contract_version": spec.contract_version, "rubric_version": spec.rubric_version,
            "profile_id": spec.profile_id, "profile_version": spec.profile_version, "input_mode": ni.input_mode,
            "unit_mode": ni.unit_mode}


def parse_ok_record(content: str, ni: NormalizedInput, spec: Spec, system: SystemInfo) -> EvaluationRecord:
    """Parse model output as a complete OK record body (all gates, verdict...). Raises OutputSchemaError."""
    try:
        data = json.loads(content)
        body = RecordBody.model_validate(data)
        record = EvaluationRecord(record_status=RecordStatus.OK, **_meta(ni, spec, system),
                                  **body.model_dump(exclude_unset=False))
    except (json.JSONDecodeError, ValueError) as exc:
        raise OutputSchemaError(str(exc)) from exc
    errs = schema_errors(record, spec.registry)
    if errs:
        raise OutputSchemaError("; ".join(errs))
    return record


def failed_record(ni: NormalizedInput, spec: Spec, system: SystemInfo, reason: str, attempts: int) -> EvaluationRecord:
    return EvaluationRecord(record_status=RecordStatus.EVALUATION_FAILED, failure=FailureInfo(
        reason=reason[:2000] or "schema-invalid output", schema_attempts=attempts), **_meta(ni, spec, system))
