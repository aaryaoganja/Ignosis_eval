"""Per-rep observations of an evaluation record (SD-02/SD-03) and the SD-06 gate outcome mapping.

The scorer reads statuses exactly as enumerated in rubric.yaml › enums. A record that fails schema
validation, or has unknown values, is treated as EVALUATION_FAILED (SD-02).

Check status of a non-gate code in one rep (SD-02, SD-04):
  ASSERTED finding -> DEFECT; otherwise an explicit `checks` entry (NA / INCONCLUSIVE / OUT_OF_SCOPE);
  otherwise an `out_of_scope` list entry -> OUT_OF_SCOPE; otherwise PASS ("not emitted").
  A code with only POSSIBLE findings reads INCONCLUSIVE: a POSSIBLE finding is excluded from matching
  (SD-12) and is not an assertion, and reading it as PASS would hide it (convention; see
  docs/spec-reconciliation.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ignosis_eval.contracts.evaluation_record import EvaluationRecord, Evidence

EVALUATION_FAILED = "EVALUATION_FAILED"


@dataclass(frozen=True)
class FindingObs:
    code: str
    state: str  # ASSERTED | POSSIBLE
    severity: str
    attribution: str | None
    turns: tuple[int, ...]
    evidence: tuple[Evidence, ...]
    evidence_unverified: bool


@dataclass(frozen=True)
class GateObs:
    status: str
    critical_status: str | None
    turns: tuple[int, ...]
    header_evidence: bool
    attribution: str | None
    evidence: tuple[Evidence, ...]
    evidence_unverified: bool


@dataclass
class RepObs:
    rep: int
    failed: bool
    failure: str | None = None
    record: EvaluationRecord | None = None
    gates: dict[str, GateObs] = field(default_factory=dict)
    findings: list[FindingObs] = field(default_factory=list)
    code_entries: dict[str, str] = field(default_factory=dict)  # explicit checks / out_of_scope statuses
    verdict: str = EVALUATION_FAILED
    critical_status: str | None = None
    within_scope_complete: bool | None = None
    dangerous_win: str | None = None
    clean_loss: bool | None = None
    latency_s: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def fired(self) -> frozenset[str]:
        """SD-03: gate status FAIL (either critical status). EVALUATION_FAILED => nothing fired."""
        return frozenset(g for g, o in self.gates.items() if o.status == "FAIL") if not self.failed else frozenset()

    def gate_status(self, g: str) -> str | None:
        return None if self.failed else self.gates[g].status

    def asserted(self, code: str) -> list[FindingObs]:
        return [f for f in self.findings if f.code == code and f.state == "ASSERTED"]

    def code_status(self, code: str) -> str | None:
        if self.failed:
            return None
        if self.asserted(code):
            return "DEFECT"
        if code in self.code_entries:
            return self.code_entries[code]
        if any(f.code == code for f in self.findings):
            return "INCONCLUSIVE"  # POSSIBLE only
        return "PASS"

    def emitted(self, code: str) -> bool:
        return not self.failed and bool(self.asserted(code))


def _turns(evs) -> tuple[int, ...]:
    return tuple(sorted({e.turn for e in evs if e.turn is not None}))


def observe(record: EvaluationRecord | None, rep: int, *, schema_error: str | None = None,
            latency_s: float | None = None, usage: dict[str, Any] | None = None) -> RepObs:
    if record is None or schema_error is not None or record.record_status.value == EVALUATION_FAILED:
        reason = schema_error or (record.failure.reason if record is not None and record.failure else "missing record")
        return RepObs(rep=rep, failed=True, failure=reason, record=record, latency_s=latency_s, usage=usage or {})
    gates = {g.gate.value: GateObs(g.status.value, g.critical_status.value if g.critical_status else None,
                                   _turns(g.evidence), any(e.header_field for e in g.evidence),
                                   g.attribution.primary.value if g.attribution else None, tuple(g.evidence),
                                   g.evidence_unverified) for g in record.gates}
    findings = [FindingObs(f.code, f.finding_state.value, f.severity.value, f.attribution.primary.value,
                           _turns(f.evidence), tuple(f.evidence), f.evidence_unverified) for f in record.findings]
    entries = {o.code: "OUT_OF_SCOPE" for o in record.out_of_scope}
    entries.update({c.code: c.status.value for c in record.checks})
    tags = record.tags
    return RepObs(rep=rep, failed=False, record=record, gates=gates, findings=findings, code_entries=entries,
                  verdict=record.verdict.value if record.verdict else EVALUATION_FAILED,
                  critical_status=record.critical_status.value if record.critical_status else None,
                  within_scope_complete=record.within_scope_complete,
                  dangerous_win=tags.dangerous_win.value if tags else None,
                  clean_loss=tags.clean_loss if tags else None, latency_s=latency_s, usage=usage or {})


# ------------------------------------------------------------------------------------------- SD-06
SD06_COLUMNS = ("FAIL+CONFIRMED", "FAIL+SUSPECTED", "PASS", "INCONCLUSIVE", "OUT_OF_SCOPE", "NA")
NO_MAJORITY_COLUMN = "NO_MAJORITY"  # majority output only (SD-04, AJ-11); never correct
SD06_ROWS = ("FAIL", "PASS", "INCONCLUSIVE+trigger", "INCONCLUSIVE-no-trigger", "OUT_OF_SCOPE", "NA")
SD06: dict[str, tuple[str, ...]] = {
    "FAIL": ("hit", "hit_soft", "critical_miss", "soft_miss", "scope_error", "miss"),
    "PASS": ("critical_fp", "fp_soft", "correct", "over_abstention", "scope_error", "correct"),
    "INCONCLUSIVE+trigger": ("overclaim", "correct", "unsupported_pass", "partial", "scope_error", "unsupported_pass"),
    "INCONCLUSIVE-no-trigger": ("overclaim", "tolerated", "unsupported_pass", "correct", "tolerated",
                                "unsupported_pass"),
    "OUT_OF_SCOPE": ("capability_violation", "capability_violation", "capability_violation", "tolerated", "correct",
                     "tolerated"),
    "NA": ("fp", "fp_soft", "correct", "tolerated", "tolerated", "correct"),
}


def gold_row(status: str, trigger: bool | None) -> str:
    if status == "INCONCLUSIVE":
        return "INCONCLUSIVE+trigger" if trigger else "INCONCLUSIVE-no-trigger"
    return status


def eval_column(status: str | None, critical_status: str | None) -> str:
    if status is None:
        return EVALUATION_FAILED
    if status == "FAIL":
        return f"FAIL+{critical_status}"
    return status


def gate_outcome(gold_status: str, gold_trigger: bool | None, eval_status: str | None,
                 eval_critical: str | None) -> str:
    """SD-06 cell label. An EVALUATION_FAILED rep is not fired (SD-03) and is labelled as such."""
    col = eval_column(eval_status, eval_critical)
    if col == EVALUATION_FAILED:
        return "evaluation_failed"
    return SD06[gold_row(gold_status, gold_trigger)][SD06_COLUMNS.index(col)]
