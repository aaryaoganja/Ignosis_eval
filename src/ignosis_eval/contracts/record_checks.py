"""SD-02 record validation against the rubric registry (beyond what the pydantic schema can check).

A record that fails these checks is a schema error, which the scorer treats as EVALUATION_FAILED and
which systems treat as a schema-invalid output (one retry, then EVALUATION_FAILED; V0 / R-03).
Unknown codes and codes that must not be emitted (NOT_EVALUATED_IN_MVP) are schema errors. Always-OOS
codes are accepted structurally so that H1 structural violations (SD-11 a) remain countable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ignosis_eval.contracts.evaluation_record import RecordBody

if TYPE_CHECKING:  # pragma: no cover
    from ignosis_eval.spec.registry import Registry


def schema_errors(body: RecordBody, registry: "Registry") -> list[str]:
    errs: list[str] = []
    known = set(registry.code_ids) | set(registry.platform_ids) | set(registry.oos_codes)
    for f in body.findings:
        if f.code in registry.gate_ids:
            errs.append(f"finding uses gate id {f.code}; gate outcomes belong in `gates`")
        elif f.code in registry.not_evaluated:
            errs.append(f"{f.code} is NOT_EVALUATED_IN_MVP and must not be emitted")
        elif f.code not in known:
            errs.append(f"unknown code {f.code}")
    for c in body.checks:
        if c.code in registry.not_evaluated:
            errs.append(f"{c.code} is NOT_EVALUATED_IN_MVP and must not be emitted")
        elif c.code not in known:
            errs.append(f"unknown check {c.code}")
    for o in body.out_of_scope:
        if o.code not in registry.oos_codes:
            errs.append(f"out_of_scope lists {o.code}, which is not an always-OOS code")
    return errs
