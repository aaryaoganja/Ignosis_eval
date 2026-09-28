"""Cross-file consistency of the spec pack (final adjudication, Step 3): the six files in docs/spec/ and the code
must carry exactly one interpretation of G1, G5, PARTIAL, confidence, repairability, the PTP outcome, majority
output, critical-status scoring and the calling window. Text checks are deliberately narrow (key phrases only)."""

from __future__ import annotations

import re

import pytest
import yaml

import factories as F
from ignosis_eval import versions as V
from ignosis_eval.contracts.enums import RUBRIC_ENUMS, ConfidenceSource, MeasurementBasis, ReasonCode
from ignosis_eval.contracts.gold_label import REPAIRABLE_CODES

SPEC = F.SPEC_DIR
SP = F.spec()
RUBRIC, PROFILE = SP.rubric, SP.profile
MD = {n: (SPEC / n).read_text(encoding="utf-8") for n in
      ("frozen-contract.md", "scoring-spec.md", "experiment-protocol.md", "implementation-blockers.md")}
CONTRACT, SCORING, PROTOCOL, BLOCKERS = (MD[n] for n in MD)


def _gate(gid: str) -> dict:
    return next(g for g in RUBRIC["gates"] if g["id"] == gid)


def _code(cid: str) -> dict:
    return next(c for c in RUBRIC["codes"] if c["id"] == cid)


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = re.search(r"\n#{1,3} ", text[start + len(heading):])
    return text[start: start + len(heading) + (nxt.start() if nxt else len(text))]


# ================================================================================ versions
def test_versions_agree_everywhere():
    assert RUBRIC["rubric_version"] == V.SPEC_RUBRIC_VERSION == "1.1-mvp"
    assert RUBRIC["contract_version"] == V.SPEC_CONTRACT_VERSION == "1.1.0-frozen"
    assert PROFILE["profile_version"] == V.SPEC_PROFILE_VERSION == "1.1.0"
    assert PROFILE["rubric_ref"] == RUBRIC["rubric_version"]
    for name, text in MD.items():
        assert "1.1.0-frozen" in text.splitlines()[0] or "`1.1.0-frozen`" in text[:600], name
        assert "1.0.0-frozen" not in text and "1.0-mvp" not in text, name
    assert "rubric 1.1-mvp" in SCORING


def test_final_adjudication_record_lists_every_decision():
    text = (SPEC / "final-adjudication.md").read_text(encoding="utf-8")
    for i in range(1, 13):
        assert f"AJ-{i:02d}" in text and f"| AJ-{i:02d} |" in CONTRACT
    for i in range(1, 15):
        assert f"FP-{i:02d}" in text


# ================================================================================ G1 (AJ-01)
def test_g1_protected_scope_single_list():
    items = ["loan_existence", "amount", "overdue_status", "loan_details"]
    assert _gate("G1")["protected_items"] == items
    assert RUBRIC["extraction_schema"]["account_disclosure"]["items"] == items
    assert PROFILE["identity_verification"]["must_precede"] == items
    sec = _section(CONTRACT, "## 9a.")
    assert re.findall(r"`(loan_existence|amount|overdue_status|loan_details)`", sec)[:4] == items
    constraint = next(ln for ln in BLOCKERS.splitlines() if "G1 scope (AJ-01" in ln)  # FP-14 authoring constraint
    assert re.findall(r"`(loan_existence|amount|overdue_status|loan_details)`", constraint) == items


# ================================================================================ G5 (AJ-03)
def test_g5_decision_table_single_definition():
    g5 = _gate("G5")
    rows = g5["decision_table"]
    assert [r["row"] for r in rows] == list(range(1, 8))
    assert [r["result"] for r in rows] == ["FAIL", "FAIL", "PASS", "INCONCLUSIVE", "PASS", "PASS", "FAIL"]
    assert rows[3]["reason_code"] == "NO_AGENT_TURN_AFTER_REQUEST" and rows[3]["trigger"] is False
    assert rows[5]["also_emits"] == "RES-06" and rows[6]["confidence"] == "MEDIUM" and rows[1]["confidence"] == "HIGH"
    assert "medium_when" not in g5 and "honor_within" not in str(g5)
    assert "NO_AGENT_TURN_AFTER_REQUEST" in RUBRIC["enums"]["reason_code"]
    assert ReasonCode.NO_AGENT_TURN_AFTER_REQUEST in list(RUBRIC_ENUMS["reason_code"])
    assert "row 6" in _code("RES-06")["description"]
    assert "decision table" in CONTRACT.lower() or "seven-row" in CONTRACT
    assert "G5" in BLOCKERS and "row" in BLOCKERS


def test_g5_code_matches_table_rows():
    """Every row result in the code is the rubric row result (FAIL rows carry HIGH/MEDIUM in the code)."""
    from ignosis_eval.engine.rules import G5_RANK

    assert sorted(G5_RANK, key=G5_RANK.__getitem__, reverse=True) == _gate("G5")["worst_result_wins"]
    code_rows = {1: {"FAIL_HIGH", "FAIL_MEDIUM"}, 2: {"FAIL_HIGH"}, 3: {"PASS"}, 4: {"INCONCLUSIVE"}, 5: {"PASS"},
                 6: {"PASS"}, 7: {"FAIL_MEDIUM"}}
    for r in _gate("G5")["decision_table"]:
        assert all(x.split("_")[0] == r["result"] for x in code_rows[r["row"]])


# ================================================================================ PARTIAL (AJ-04)
def test_partial_single_definition():
    v7 = next(v for v in RUBRIC["verdict_rules"] if v["id"] == "V7")["rule"]
    assert "PARTIAL" in v7 and "INCONCLUSIVE" in v7 and "OUT_OF_SCOPE" in v7 and "NOT_EVALUATED_IN_MVP" in v7
    assert "within_scope_complete = (evaluability == EVALUABLE) AND no POSSIBLE" in v7
    sec = _section(CONTRACT, "## 5.")
    assert "PARTIAL" in sec and "never changes the verdict" in sec
    assert "EVALUABILITY-PARTIAL" not in str(RUBRIC["evaluability_checks"])  # derived, not a front-end check


# ================================================================================ confidence (AJ-05, AJ-06)
def test_confidence_single_interpretation():
    cr = RUBRIC["confidence_rules"]
    assert cr["low_when_any"] == ["cited_span_unreliable", "contradictory_sources"]
    assert "role_confidence" not in str(cr["low_when_any"])
    assert RUBRIC["enums"]["confidence_source"] == [c.value for c in ConfidenceSource]
    arch = RUBRIC["architecture_application"]
    assert arch["A"]["confidence_source"] == "SELF_REPORTED" and arch["A"]["llm_calls"] == 1
    assert {arch[k]["confidence_source"] for k in ("A_PLUS", "B", "K0")} == {"COMPUTED"}
    assert arch["A_PLUS"]["llm_calls"] == 0 and len(arch["A_PLUS"]["ordered_steps"]) == 8
    dc00 = next(c for c in RUBRIC["evaluability_checks"] if c["id"] == "DC-00")["rule"]
    assert "Call-level" in dc00 and "no AGENT turn" in dc00 and "no BORROWER turn" in dc00
    th = PROFILE["thresholds"]
    assert th["role_confidence_min"]["value"] == 0.85 and "call-level" in th["role_confidence_min"]["note"]
    assert th["diarization_turn_min_confidence"]["value"] == "PENDING_HUMAN_SIGNOFF"
    assert "SELF_REPORTED" in CONTRACT and "SELF_REPORTED" in PROTOCOL


# ================================================================================ repairability (AJ-08)
def test_repair_allowlist_single_definition():
    allow = RUBRIC["repair_rules"]["allowlist"]
    assert allow == ["ACC-05"]
    flagged = [c["id"] for c in RUBRIC["codes"] + RUBRIC["gates"] if c.get("repairable")]
    assert flagged == allow == list(SP.registry.repair_allowlist) == sorted(REPAIRABLE_CODES)
    assert RUBRIC["repair_rules"]["gates_repairable"] is False
    assert "informational" not in str(_code("ACC-05")).lower()
    assert not re.search(r"minor\s*(->|→)\s*informational", yaml.safe_dump(RUBRIC).lower())
    for text in (CONTRACT, SCORING, PROTOCOL, BLOCKERS):  # the only mentions are removal statements
        for line in text.splitlines():
            if re.search(r"Minor\s*(->|→)\s*Informational", line):
                assert re.search(r"is removed|There is no Minor→Informational", line), line
    assert "Only **ACC-05** is repairable" in CONTRACT


# ================================================================================ PTP outcome (AJ-09)
def test_ptp_positive_single_rule():
    om = RUBRIC["outcome_model"]
    assert om["positive_ptp_requires_firmness"] == ["firm"]
    assert "firm" in om["positive_ptp_rule"] and "partial" in om["positive_ptp_rule"]
    assert "firmness `firm`" in CONTRACT
    reg = SP.registry
    assert reg.outcome_positive(["PTP_STATED"], "firm")
    assert not reg.outcome_positive(["PTP_STATED"], "soft") and not reg.outcome_positive(["PTP_STATED"], None)


# ================================================================================ majority (AJ-11)
def test_majority_single_rule():
    sd04 = _section(SCORING, "## SD-04")
    assert "≥3 of 5" in sd04 and "NO_MAJORITY" in sd04 and "no tie-breaking" in sd04
    assert "2 FAIL + 2 PASS + 1 EVALUATION_FAILED" in sd04
    assert not re.search(r"precedence|tie-break(?!ing\.)(?!, no)", sd04.replace("no tie-breaking", "")
                         .replace("no tie-break needed", ""), re.I)
    assert "NO_MAJORITY" in _section(SCORING, "## SD-06") and "NO_MAJORITY" in _section(SCORING, "## SD-17")
    from ignosis_eval.metrics import alignment, majority

    assert majority.NO_MAJORITY == alignment.NO_MAJORITY_COLUMN == "NO_MAJORITY"
    assert majority.threshold(5) == 3


# ================================================================================ critical-status scoring (AJ-12)
def test_critical_status_scoring_single_rule():
    sd17 = _section(SCORING, "## SD-17")
    assert "no critical-status mismatch metric" in sd17 and "descriptively" in sd17
    from ignosis_eval.contracts.gold_label import GoldGate

    assert "critical_status" not in GoldGate.model_fields
    import ignosis_eval.metrics.compute as compute

    src = open(compute.__file__, encoding="utf-8").read()
    assert "critical_status_mismatch" not in src and "def critical_status_split" in src


# ================================================================================ calling window (AJ-10)
def test_calling_window_single_definition():
    cw = PROFILE["calling_window"]
    assert (cw["start"], cw["end"], cw["timezone"], cw["status"]) == ("08:00", "19:00", "Asia/Kolkata",
                                                                      "FROZEN_FOR_ASSIGNMENT")

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)
        else:
            yield o

    assert "REFERENCE" not in set(map(str, walk(PROFILE)))
    assert "calling_window" in str(_gate("G7")["dependencies"])


# ================================================================================ TRT-06 (AJ-02) and extraction (AJ-07)
def test_trt06_measurement_rule_single_definition():
    rule = _code("TRT-06")["measurement_rule"]
    assert rule == {"duration_when": "turn_has_timestamps", "otherwise": "word_count", "record_field":
                    "measurement_basis"}
    assert RUBRIC["enums"]["measurement_basis"] == [m.value for m in MeasurementBasis]
    th = PROFILE["thresholds"]
    assert (th["monologue_max_seconds"]["value"], th["monologue_max_words"]["value"]) == (30, 80)
    assert "30 s" in BLOCKERS and "80 words" in BLOCKERS


def test_extraction_schema_version_single_definition():
    assert RUBRIC["extraction_schema"]["version"] == V.EXTRACTION_SCHEMA


@pytest.mark.parametrize("name", ["rubric.yaml", "profile.yaml"])
def test_no_pending_value_is_defaulted(name):
    """PENDING_HUMAN_SIGNOFF values stay pending (the adjudication selects none of them)."""
    from ignosis_eval.spec.pending import KNOWN

    assert "profile.thresholds.diarization_turn_min_confidence.value" in KNOWN
    text = (SPEC / name).read_text(encoding="utf-8")
    assert text.count("PENDING_HUMAN_SIGNOFF") >= 1
