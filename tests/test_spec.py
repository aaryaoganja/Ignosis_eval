"""The spec pack is the authority: versions, enum vocabularies, capability CONDITIONALs, named sets and the
PENDING_HUMAN_SIGNOFF inventory are read from docs/spec and must not drift."""

from __future__ import annotations

import hashlib

import pytest
import yaml

import factories as F
from ignosis_eval.contracts.enums import RUBRIC_ENUMS
from ignosis_eval.spec.loader import SPEC_FILES, PendingHumanSignoffError, SpecError, load_spec
from ignosis_eval.spec.pending import find_pending
from ignosis_eval.spec.registry import CONDITIONAL_RULES

EXPECTED_PENDING = {
    "profile.overall_signoff.status": True,
    "profile.consequences.approved_statement_wording.status": False,
    "profile.vulnerability_protocol.helpline.status": False,
    "profile.thresholds.loop_similarity.value": True,
    "profile.thresholds.asr_low_confidence_word.value": True,
    "profile.thresholds.asr_unreliable_call_share.value": True,
    "profile.thresholds.material_span_min_confidence.value": True,
    "profile.thresholds.non_conversation_min_borrower_words.value": True,
    "profile.thresholds.overlap_min_seconds.value": True,
    "profile.thresholds.high_friction_min_minor_count.value": False,
    "profile.lexicons.review_status": True,
    "rubric.evaluability_checks[DC-LANG].detection": True,
    "rubric.platform_signals[PLT-01].severity_escalation": False,
    "rubric.platform_signals[PLT-02].severity_escalation": False,
    "rubric.platform_signals[PLT-03].severity_escalation": False,
    "rubric.outcome_model.primary_disposition_precedence": False,
    "rubric.tags.high_friction.threshold": False,
}


def test_spec_pack_complete_and_versions(spec):
    assert all((F.SPEC_DIR / f).exists() for f in SPEC_FILES)
    assert (spec.contract_version, spec.rubric_version, spec.profile_id) == ("1.0.0-frozen", "1.0-mvp",
                                                                           "collections_default_v1")
    assert spec.is_canonical_profile
    for name, sha in spec.file_sha256.items():
        assert sha == hashlib.sha256((F.SPEC_DIR / name).read_bytes()).hexdigest()


def test_enums_mirror_rubric_exactly(spec):
    for name, enum_cls in RUBRIC_ENUMS.items():
        assert [e.value for e in enum_cls] == spec.enum_values(name), name
    assert set(RUBRIC_ENUMS) == set(spec.rubric["enums"])


def test_conditional_rules_match_rubric(spec):
    found = {(cid, m.value) for cid, cd in spec.registry.checks.items() for m, a in cd.applicable.items()
             if a.value == "CONDITIONAL"}
    assert found == {(c, m.value) for c, m in CONDITIONAL_RULES}


def test_named_sets_and_code_groups(spec):
    reg = spec.registry
    assert set(reg.borrower_impact_majors) == {"UND-01", "UND-02", "UND-03", "ACC-03u", "COM-02", "COM-05",
                                               "RES-01", "RES-04"}
    assert set(reg.dw_inducement_set) == {"UND-01", "UND-02", "RES-01", "TRT-01", "TRT-02", "COM-02", "COM-05",
                                          "ACC-05"}
    assert set(reg.minor_codes) == {"UND-12", "RES-11", "TRT-06", "COM-03"}  # §4.1 (COM-03 callback variant)
    majors = {"UND-01", "UND-02", "UND-03", "UND-04", "UND-05", "UND-06", "ACC-03u", "ACC-04", "ACC-05", "RES-01",
              "RES-02", "RES-03", "RES-04", "RES-05", "RES-06", "COM-01", "COM-02", "COM-03", "COM-05", "COM-06",
              "TRT-01", "TRT-02", "TRT-03", "POL-01"}
    assert set(reg.major_codes) == majors  # §4.1 Major list
    assert set(reg.oos_codes) >= {"ACC-01", "ACC-02", "ACC-06", "EXE-01", "OUTCOME_VERIFIED", "COMPROMISED_WIN"}


def test_pending_inventory_is_preserved(spec):
    items = {p.path: p.blocks_locked_run for p in find_pending(spec.rubric, spec.profile)}
    assert items == EXPECTED_PENDING


def test_pending_threshold_raises_and_never_defaults(spec):
    with pytest.raises(PendingHumanSignoffError):
        spec.threshold("loop_similarity")
    assert spec.threshold("quote_match_min") == 90
    assert spec.lexicon_review_pending()
    assert spec.lexicon_terms("prohibited_consequences", next(iter(spec.profile["lexicons"]["prohibited_consequences"]))
                              ) == []


def test_version_mismatch_fails_closed(tmp_path):
    for f in SPEC_FILES:
        (tmp_path / f).write_bytes((F.SPEC_DIR / f).read_bytes())
    prof = yaml.safe_load((tmp_path / "profile.yaml").read_text())
    prof["profile_id"] = "something_else"
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump(prof))
    with pytest.raises(SpecError):
        load_spec(tmp_path)


def test_test_profile_is_not_canonical(tmp_path):
    s = load_spec(F.SPEC_DIR, profile_path=F.test_profile(tmp_path))
    assert not s.is_canonical_profile
