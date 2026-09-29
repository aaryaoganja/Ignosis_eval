"""Cross-file consistency of the frozen spec pack (contract 1.2.0-frozen, rubric 1.2-mvp, profile 1.1.1): the six
files in docs/spec/, the freeze records in docs/freeze/ and the code must carry exactly one interpretation of each
adjudicated decision (AJ-01..AJ-12, SC-01..SC-08, BD-01, BD-02). Text checks are deliberately narrow (key phrases
only)."""

from __future__ import annotations

import re

import pytest
import yaml

import factories as F
from ignosis_eval import versions as V
from ignosis_eval.contracts.enums import RUBRIC_ENUMS, ConfidenceSource, MeasurementBasis, ReasonCode
from ignosis_eval.contracts.gold_label import REPAIRABLE_CODES

SPEC = F.SPEC_DIR
FREEZE = F.REPO / "docs" / "freeze"
SP = F.spec()
RUBRIC, PROFILE = SP.rubric, SP.profile
MD = {n: (SPEC / n).read_text(encoding="utf-8") for n in
      ("frozen-contract.md", "scoring-spec.md", "experiment-protocol.md", "implementation-blockers.md")}
CONTRACT, SCORING, PROTOCOL, BLOCKERS = (MD[n] for n in MD)
STAGE5_IDS = [f"SC-0{i}" for i in range(1, 9)] + ["BD-01", "BD-02"]


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
    assert RUBRIC["rubric_version"] == V.SPEC_RUBRIC_VERSION == "1.2-mvp"
    assert RUBRIC["contract_version"] == V.SPEC_CONTRACT_VERSION == "1.2.0-frozen"
    assert PROFILE["profile_version"] == V.SPEC_PROFILE_VERSION == "1.1.1"
    assert PROFILE["rubric_ref"] == RUBRIC["rubric_version"]
    for name, text in MD.items():
        assert "`1.2.0-frozen`" in text[:600], name
        assert "1.1.0-frozen" not in text and "1.0.0-frozen" not in text, name
    assert "rubric 1.2-mvp" in SCORING
    handoff = (FREEZE / "FREEZE-HANDOFF.md").read_text(encoding="utf-8")
    assert "Contract: `1.2.0-frozen`" in handoff and "Rubric: `1.2-mvp`" in handoff
    assert "`collections_default_v1` `1.1.1`" in handoff


def test_stage5_log_is_a_verbatim_extract_of_contract_section_0():
    """docs/freeze/STAGE5-ADJUDICATION-LOG.md: 'if this extract ever differs from it, the contract governs and the
    difference is a bug' — so every decision row of the log must appear verbatim in contract §0."""
    log = (FREEZE / "STAGE5-ADJUDICATION-LOG.md").read_text(encoding="utf-8")
    rows = [ln for ln in log.splitlines() if re.match(r"^\| (SC|BD)-\d\d \|", ln)]
    assert [r.split("|")[1].strip() for r in rows] == STAGE5_IDS
    sec0 = _section(CONTRACT, "## 0.")
    for row in rows:
        assert row in sec0, row
    for i in range(1, 13):
        assert f"| AJ-{i:02d} |" in sec0
    assert not (SPEC / "final-adjudication.md").exists()  # the 1.1 reconstruction is superseded (docs/history/)


# ================================================================================ G1 (AJ-01)
def test_g1_protected_scope_single_list():
    items = ["loan_existence", "amount", "overdue_status", "loan_details"]
    ad = RUBRIC["extraction_schema"]["account_disclosure"]
    assert list(ad["definitions"]) == items and all(i in ad["items"] for i in items)
    assert PROFILE["identity_verification"]["must_precede"] == items
    sec = _section(CONTRACT, "## 9.")
    assert "**9a. Disclosure scope (AJ-01).**" in sec
    assert re.findall(r"`(loan_existence|amount|overdue_status|loan_details)`", sec)[:4] == items
    assert "extraction_schema.account_disclosure" in _gate("G1")["description"]


# ================================================================================ G5 (AJ-03)
def test_g5_decision_table_single_definition():
    g5 = _gate("G5")
    rows = g5["decision_table"]
    assert [r["order"] for r in rows] == list(range(1, 8))
    assert [r["result"] for r in rows] == ["FAIL", "FAIL", "PASS", "INCONCLUSIVE", "PASS", "PASS", "FAIL"]
    assert rows[3]["reason_code"] == "NO_AGENT_TURN_AFTER_REQUEST" and rows[3]["trigger"] is False
    assert rows[5]["also_emit"] == "RES-06" and rows[6]["confidence"] == "MEDIUM" and rows[1]["confidence"] == "HIGH"
    assert set(g5["definitions"]) == {"N", "honoring_event", "ct", "refusal"}
    assert set(g5["definitions"]["honoring_event"]) == {"human_request", "stop_request"}
    assert "NO_AGENT_TURN_AFTER_REQUEST" in RUBRIC["enums"]["reason_code"]
    assert ReasonCode.NO_AGENT_TURN_AFTER_REQUEST in list(RUBRIC_ENUMS["reason_code"])
    assert "decision_table order 6" in _code("RES-06")["description"]
    assert "G5.decision_table" in CONTRACT


def test_g5_code_matches_table_entries():
    """Every entry result in the code is the rubric result (FAIL entries carry HIGH/MEDIUM in the code)."""
    from ignosis_eval.engine.rules import G5_RANK

    assert "(FAIL > INCONCLUSIVE > PASS)" in " ".join(_gate("G5")["description"].split())
    assert [k.split("_")[0] for k in sorted(G5_RANK, key=G5_RANK.__getitem__, reverse=True)] == \
        ["FAIL", "FAIL", "INCONCLUSIVE", "PASS"]
    code_rows = {1: {"FAIL_HIGH", "FAIL_MEDIUM"}, 2: {"FAIL_HIGH"}, 3: {"PASS"}, 4: {"INCONCLUSIVE"}, 5: {"PASS"},
                 6: {"PASS"}, 7: {"FAIL_MEDIUM"}}
    for r in _gate("G5")["decision_table"]:
        assert all(x.split("_")[0] == r["result"] for x in code_rows[r["order"]])


# ================================================================================ SC-01 .. SC-08 (Stage 5)
def test_sc01_terminal_trigger_single_definition():
    rule = RUBRIC["attribution_rules"]["terminal_trigger_rule"]
    assert "EXCEPT G5" in rule["applies_to"] and "NO_AGENT_TURN_AFTER_TRIGGER" in rule["rule"]
    assert "NO_AGENT_TURN_AFTER_TRIGGER" in RUBRIC["enums"]["reason_code"]
    assert ReasonCode.NO_AGENT_TURN_AFTER_TRIGGER.value == "NO_AGENT_TURN_AFTER_TRIGGER"
    assert "`NO_AGENT_TURN_AFTER_TRIGGER` (check-level, other non_response checks; SC-01)" in CONTRACT


def test_sc02_reliability_precondition_present():
    div = next(c for c in RUBRIC["evaluability_checks"] if c["id"] == "DC-DIV")
    pre = div["reliability_precondition"]
    assert "SC-02" in pre and "BOTH source spans are reliable" in pre


def test_sc03_evaluability_order_single_definition():
    order = RUBRIC["evaluability_order"]
    assert len(order) == 5 and "DC-02" in order[2] and "no-BORROWER-turn" in order[3] and "only if DC-02" in order[3]
    dc00 = next(c for c in RUBRIC["evaluability_checks"] if c["id"] == "DC-00")
    assert "SC-03" in dc00["order_note"]
    assert "(SC-03: `NON_CONVERSATIONAL` is checked first)" in CONTRACT


def test_sc04_sd08_admits_na():
    sd08 = _section(SCORING, "## SD-08")
    assert "gold(u,g) ∈ {PASS, NA}" in sd08.split("\n")[1] and "SC-04" in sd08


def test_sc05_opaque_aliases_in_protocol():
    p17 = _section(PROTOCOL, "## P-17")
    assert "u_7f3a91c2" in p17 and "^(G|M|K|C|R|P|J|X|A|AB|E|S|MI|MC|MD|SN|RT|CAL)-" in p17
    from ignosis_eval.runner.aliases import ITEM_ID_PREFIX_PATTERN

    assert ITEM_ID_PREFIX_PATTERN == "^(G|M|K|C|R|P|J|X|A|AB|E|S|MI|MC|MD|SN|RT|CAL)-"


def test_sc06_contract_section_12_carries_no_holdout_intent():
    sec12 = CONTRACT[CONTRACT.index("## 12."):CONTRACT.index("## 13.")]
    table = [ln for ln in sec12.splitlines() if ln.startswith("| ")]
    assert table[0] == "| ID | Split | Pair |"  # no intent column
    assert all(len(ln.strip("|").split("|")) == 3 for ln in table)
    assert "BENCH_PRIVATE_DIR" in sec12


def test_sc08_g6_na_when_non_explicit():
    assert "SC-08" in _gate("G6")["na_when"] and "DISTRESS_NONEXPLICIT" in _gate("G6")["na_when"]


def test_bd02_no_g7_positive_statement():
    handoff = (FREEZE / "FREEZE-HANDOFF.md").read_text(encoding="utf-8")
    assert "**There is no G7 positive in bench-a1.**" in handoff
    assert "**bench-a1 contains no G7 positive**" in CONTRACT


# ================================================================================ PARTIAL (AJ-04)
def test_partial_single_definition():
    v7 = next(v for v in RUBRIC["verdict_rules"] if v["id"] == "V7")
    assert "PARTIAL" in v7["rule"] and "INCONCLUSIVE" in v7["rule"]
    assert "within_scope_complete = (evaluability_status = EVALUABLE) AND no POSSIBLE findings" in v7["rule"]
    assert v7["partial_excludes"] == ["OUT_OF_SCOPE checks", "gates reported FAIL/SUSPECTED from an in-span trigger",
                                      "not_evaluated_in_mvp codes"]
    assert v7["partial_affects_verdict"] is False
    sec = _section(CONTRACT, "## 5.")
    assert "PARTIAL" in sec and "it has no effect on the verdict" in sec
    assert "EVALUABILITY-PARTIAL" not in str(RUBRIC["evaluability_checks"])  # derived, not a front-end check


# ================================================================================ confidence (AJ-05, AJ-06)
def test_confidence_single_interpretation():
    cr = RUBRIC["confidence_rules"]
    assert cr["low_when_any"] == ["cited_span_unreliable", "contradictory_sources"]
    assert RUBRIC["enums"]["confidence_source"] == [c.value for c in ConfidenceSource]
    arch = RUBRIC["architecture_application"]
    assert set(arch) == {"shared_front_end_all_systems", "A", "A_plus", "B"}
    assert arch["A"]["confidence_source"] == "SELF_REPORTED"
    assert {arch[k]["confidence_source"] for k in ("A_plus", "B")} == {"COMPUTED"}
    assert arch["A_plus"]["llm_calls"] == 0 and len(arch["A_plus"]["ordered_steps"]) == 8
    assert "role mismatch" in arch["A_plus"]["ordered_steps"]["4_confidence_cap"]
    dc00 = next(c for c in RUBRIC["evaluability_checks"] if c["id"] == "DC-00")["rule"]
    assert "call-level" in dc00 and "no AGENT turn" in dc00 and "no BORROWER turn" in dc00
    th = PROFILE["thresholds"]
    assert th["role_confidence_min"]["value"] == 0.85
    assert th["diarization_turn_min_confidence"]["value"] == "PENDING_HUMAN_SIGNOFF"
    assert "SELF_REPORTED" in CONTRACT


# ================================================================================ repairability (AJ-08)
def test_repair_allowlist_single_definition():
    rr = RUBRIC["repair_rules"]
    assert rr["allowlist"] == ["ACC-05"] and rr["minor_to_informational"] == "removed"
    flagged = [c["id"] for c in RUBRIC["codes"] + RUBRIC["gates"] if c.get("repairable")]
    assert flagged == rr["allowlist"] == list(SP.registry.repair_allowlist) == sorted(REPAIRABLE_CODES)
    assert rr["gates_repairable"] is False
    assert "informational" not in str(_code("ACC-05")).lower()
    assert "corrects the conflicting value" in rr["major_to_minor_when"]
    assert "Only the codes on the allowlist `{ACC-05}` are repairable." in CONTRACT


# ================================================================================ PTP outcome (AJ-09)
def test_ptp_positive_single_rule():
    om = RUBRIC["outcome_model"]
    assert "positive_ptp_requires_firmness" not in om  # 1.2 states the rule as prose only
    assert om["positive_ptp_rule"].startswith("PTP_STATED is positive iff commitment firmness = firm")
    assert "firmness = `firm`" in CONTRACT
    reg = SP.registry
    assert reg.positive_ptp_requires_firmness == ("firm",)
    assert reg.outcome_positive(["PTP_STATED"], "firm")
    assert not reg.outcome_positive(["PTP_STATED"], "soft") and not reg.outcome_positive(["PTP_STATED"], None)


def test_registry_fails_closed_when_the_ptp_rule_changes_shape():
    from ignosis_eval.spec.registry import Registry, RegistryError

    broken = yaml.safe_load(yaml.safe_dump(RUBRIC))
    broken["outcome_model"]["positive_ptp_rule"] = "PTP_STATED is positive when the borrower sounds sure"
    with pytest.raises(RegistryError, match="positive_ptp_rule"):
        Registry.from_rubric(broken)
    broken["outcome_model"]["positive_ptp_rule"] = "PTP_STATED is positive iff commitment firmness = strong"
    with pytest.raises(RegistryError):
        Registry.from_rubric(broken)


# ================================================================================ majority (AJ-11)
def test_majority_single_rule():
    sd04 = _section(SCORING, "## SD-04")
    assert "≥3 of 5" in sd04 and "NO_MAJORITY" in sd04 and "no modal tie-breaking anywhere" in sd04
    assert "2 FAIL + 2 PASS + 1 EVALUATION_FAILED" in sd04
    assert "else `PASS` if the code is neither emitted nor abstained in ≥3 reps" in sd04
    assert "NO_MAJORITY" in _section(SCORING, "## SD-06") and "NO_MAJORITY" in _section(SCORING, "## SD-17")
    from ignosis_eval.metrics import alignment, majority

    assert majority.NO_MAJORITY == alignment.NO_MAJORITY_COLUMN == "NO_MAJORITY"
    assert majority.threshold(5) == 3


# ================================================================================ critical-status scoring (AJ-12)
def test_critical_status_scoring_single_rule():
    sd17 = _section(SCORING, "## SD-17")
    assert "no mismatch metric" in sd17 and "descriptive only" in sd17 and "for fired gold-FAIL units" in sd17
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
    trt = _code("TRT-06")
    assert "otherwise word count (AJ-02)" in trt["measurement_rule"]
    assert trt["finding_field"] == "measurement_basis: duration | words"
    assert [m.value for m in MeasurementBasis] == [p.strip() for p in trt["finding_field"].split(":")[1].split("|")]
    assert "measurement_basis" not in RUBRIC["enums"]  # no longer a rubric enum in 1.2
    th = PROFILE["thresholds"]
    assert (th["monologue_max_seconds"]["value"], th["monologue_max_words"]["value"]) == (30, 80)


def test_extraction_schema_single_definition():
    assert "version" not in RUBRIC["extraction_schema"]  # the schema version is the implementation's
    assert V.EXTRACTION_SCHEMA == "extraction/2.0.0"
    assert "JSON Schema is generated from this" in (SPEC / "rubric.yaml").read_text(encoding="utf-8")


def test_authoring_constraints_are_the_frozen_eight():
    sec = BLOCKERS[BLOCKERS.index("## Authoring constraints for B-01 and B-03 (frozen)"):]
    sec = sec[: sec.index("\n## ", 5)]
    nums = re.findall(r"^(\d+)\. ", sec, re.M)
    assert nums == [str(i) for i in range(1, 9)]
    assert "Borrower turns that directly react to an edited agent turn may change" in sec


@pytest.mark.parametrize("name", ["rubric.yaml", "profile.yaml"])
def test_no_pending_value_is_defaulted(name):
    """PENDING_HUMAN_SIGNOFF values stay pending (no adjudication selects any of them)."""
    from ignosis_eval.spec.pending import KNOWN

    assert "profile.thresholds.diarization_turn_min_confidence.value" in KNOWN
    text = (SPEC / name).read_text(encoding="utf-8")
    assert text.count("PENDING_HUMAN_SIGNOFF") >= 1
