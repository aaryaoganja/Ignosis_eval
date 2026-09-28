"""Frozen Stage 5 DEV design in bench/public: integrity, DEV-only content, and the PDxxx validation rules.

The design files are human-authored and stored verbatim; these tests never write transcript wording. Mutations are
applied to temporary copies only (re-hash-listed with `write_manifest(replace=True)` so each test isolates one rule).
"""

from __future__ import annotations

import csv
import io
import shutil
from pathlib import Path

import pytest
import yaml

import factories as F
from ignosis_eval.benchmark.checks import check_bench
from ignosis_eval.benchmark.layout import PRIVATE_ENV, BenchLayout
from ignosis_eval.benchmark.public_dev import (
    ALLOWED_FILES,
    DATA_FILES,
    PublicDevError,
    frozen_dev,
    parse_case_cards,
    resolve_beats,
    validate_public_dev,
    write_manifest,
)
from ignosis_eval.contracts.benchmark import ItemMeta
from ignosis_eval.contracts.enums import Pack, UnitMode

PUBLIC = F.REPO / "bench" / "public"
CONTRACT = (F.SPEC_DIR / "frozen-contract.md").read_text(encoding="utf-8")
SP = F.spec()

# The open warnings of the ingested design, pinned so any change to the design or the rules is visible.
EXPECTED_WARNINGS = sorted([
    ("PD011", None),       # MP-01: G7 PASS (G-02 has a header) vs OUT_OF_SCOPE (M-01 has none)
    ("PD011", None),       # MP-10: same for K-07 vs C-08
    ("PD014", "C-10"),     # G5 element continued_collection_turns (rubric: continued_collection_turns_or_refusal_turn)
    ("PD014", "MD-G1"),    # POL-01 element window_end_turn (rubric: window_or_statement_turn)
    ("PD014", "MD-G5"),    # G5 row-7 element closing_turn has no rubric name
    ("PD015", "C-01"),     # third_party_signal_turn spoken by the third party (rubric role borrower)
    ("PD015", "C-01"),     # no borrower beat: the third party must be labeled BORROWER (DC-00)
    ("PD015", "K-01"),     # same
    ("PD015", "MD-G6"),    # G6 cue_turn spoken by the relative
    ("PD015", "MD-G6"),    # G1 third_party_signal_turn spoken by the relative
    ("PD015", "MD-G6"),    # no borrower beat
    ("PD016", "K-01"),     # control target G1 has gold NA: outside the SD-08 targeted universe
    ("PD016", "K-07"),     # control target G4 has gold NA
], key=lambda x: (x[0], x[1] or ""))


def _rules(rep) -> set[str]:
    return {i.rule_id for i in rep.errors}


@pytest.fixture
def pub(tmp_path: Path) -> Path:
    d = tmp_path / "public"
    shutil.copytree(PUBLIC, d)
    return d


def _rehash(d: Path) -> None:
    write_manifest(d, replace=True)


def _card_section(text: str, iid: str) -> tuple[int, int]:
    start = text.index(f"### {iid} — ")
    end = text.find("\n### ", start + 1)
    return start, (end if end >= 0 else len(text))


def edit_card(d: Path, iid: str, old: str, new: str) -> None:
    p = d / DATA_FILES["cards"]
    text = p.read_text(encoding="utf-8")
    a, b = _card_section(text, iid)
    assert old in text[a:b], (iid, old)
    p.write_bytes((text[:a] + text[a:b].replace(old, new, 1) + text[b:]).encode("utf-8"))


def edit_matrix(d: Path, iid: str, **values: str) -> None:
    p = d / DATA_FILES["matrix"]
    rows = list(csv.DictReader(io.StringIO(p.read_bytes().decode("utf-8"), newline="")))
    fields = list(rows[0])
    for r in rows:
        if r["item_id"] == iid:
            r.update(values)
    out = io.StringIO(newline="")
    w = csv.DictWriter(out, fieldnames=fields, lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    p.write_bytes(out.getvalue().encode("utf-8"))


def edit_blueprint(d: Path, iid: str, fn) -> None:
    p = d / DATA_FILES["blueprint"]
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    fn(next(i for i in data["items"] if i["item_id"] == iid))
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


# ================================================================================ the ingested files
def test_public_dir_holds_exactly_the_frozen_dev_design():
    assert {p.name for p in PUBLIC.iterdir()} == set(ALLOWED_FILES)
    rep = validate_public_dev(PUBLIC, SP)
    assert not [i for i in rep.issues if i.rule_id == "PD001"]  # manifest verifies


def test_ingested_design_validates_against_the_spec():
    rep = validate_public_dev(PUBLIC, SP)
    assert rep.errors == [], "\n".join(map(str, rep.errors))
    got = sorted(((i.rule_id, i.item_id) for i in rep.warnings), key=lambda x: (x[0], x[1] or ""))
    assert got == EXPECTED_WARNINGS, "\n".join(map(str, rep.warnings))


def test_design_facts():
    d = validate_public_dev(PUBLIC, SP).design
    assert d is not None and len(d.items) == 25
    packs = {p: sum(it.pack is p for it in d.items.values()) for p in Pack}
    assert (packs[Pack.CORE], packs[Pack.MICRO], packs[Pack.MODALITY], packs[Pack.SNIPPET]) == (10, 8, 1, 6)
    assert {it.split for it in d.items.values()} == {"dev"}
    g02, n5 = d.items["G-02"], d.items["G-02-N5"]
    assert g02.unit_modes == [UnitMode.TRANSCRIPT, UnitMode.T_GOLD, UnitMode.T_ASR, UnitMode.A, UnitMode.A_T]
    assert g02.has_header and g02.gates is not None and g02.gates["G7"] == "PASS"
    assert n5.unit_modes == [UnitMode.A, UnitMode.A_T] and n5.tuning_only and not n5.scored
    assert all(not it.scored and it.gates is None for it in d.items.values() if it.pack is Pack.SNIPPET)
    assert d.items["MD-G4"].gates["G4"] == "INCONCLUSIVE+trigger"  # type: ignore[index]
    reg = d.registries()
    assert [(p.pair_id, p.clean_item, p.violating_item, p.target_check) for p in reg.pairs] == [
        ("MP-01", "G-02", "M-01", "COM-02"), ("MP-04", "K-01", "C-01", "G1"), ("MP-10", "K-07", "C-08", "G4")]
    assert [(c.item_id, [g.value for g in c.target_gates]) for c in reg.controls] == [
        ("K-01", ["G1"]), ("K-07", ["G4"]), ("MD-C1", ["G5"]), ("MD-C2", ["G6"])]


def test_design_matches_frozen_contract_dev_entries():
    fz = frozen_dev(CONTRACT)
    d = validate_public_dev(PUBLIC, SP).design
    assert d is not None and set(d.items) == set(fz.ids)
    assert {iid: it.pair for iid, it in d.items.items() if it.pair} == fz.pairs
    assert fz.audio_renderings == {"G-02", "M-01"}


def test_beats_resolve_inherited_ranges():
    cards = parse_case_cards((PUBLIC / DATA_FILES["cards"]).read_text(encoding="utf-8"))
    beats = resolve_beats(cards)
    assert beats["C-01"][2].speaker == "T" and beats["C-01"][7].speaker == "A"  # B1-B6 inherited from K-01
    assert set(beats["G-02-N5"]) == set(beats["G-02"])                           # "as G-02"
    assert beats["M-01"][4].speaker == "C" and 1 in beats["M-01"]


# ================================================================================ DEV only, fail closed
def test_non_dev_split_rejected(pub):
    edit_matrix(pub, "C-03", split="holdout")
    _rehash(pub)
    assert "PD003" in _rules(validate_public_dev(pub, SP))


def test_redteam_pack_rejected(pub):
    edit_matrix(pub, "C-03", pack="redteam")
    _rehash(pub)
    assert "PD003" in _rules(validate_public_dev(pub, SP))


def test_id_outside_the_frozen_dev_set_rejected(pub):
    contract = "\n".join(ln for ln in CONTRACT.splitlines() if not ln.startswith("| C-03 |"))
    rep = validate_public_dev(pub, SP, contract_md=contract)
    assert any(i.rule_id == "PD003" and i.item_id == "C-03" for i in rep.errors)


def test_unexpected_file_rejected(pub):
    (pub / "extra-matrix.csv").write_text("item_id\nZZ-X01\n", encoding="utf-8")
    rep = validate_public_dev(pub, SP)
    assert "PD001" in _rules(rep) and rep.design is None


def test_hash_drift_fails_closed(pub):
    p = pub / DATA_FILES["matrix"]
    p.write_bytes(p.read_bytes() + b"\r\n")
    rep = validate_public_dev(pub, SP)
    assert "PD001" in _rules(rep) and rep.design is None


def test_manifest_is_write_once(pub):
    with pytest.raises(PublicDevError, match="write-once"):
        write_manifest(pub)


def test_private_benchmark_directories_are_never_required(monkeypatch, tmp_path):
    monkeypatch.delenv(PRIVATE_ENV, raising=False)
    layout = BenchLayout(F.REPO / "bench")
    assert layout.private_root is None
    rep = check_bench(layout, SP, scopes=("dev",), require_gold=True)
    assert rep.ok and rep.public_dev is not None and rep.public_dev.design is not None
    assert layout.public_dir in layout.protected_paths()
    monkeypatch.setenv(PRIVATE_ENV, str(tmp_path / "does-not-exist"))
    assert check_bench(BenchLayout(F.REPO / "bench"), SP, scopes=("dev",)).ok


# ================================================================================ consistency rules
def test_cross_file_disagreement(pub):
    edit_blueprint(pub, "C-08", lambda it: it.update(verdict="MEETS_BAR"))
    _rehash(pub)
    assert {"PD004", "PD006"} <= _rules(validate_public_dev(pub, SP))


def test_expected_verdict_consistency(pub):
    edit_matrix(pub, "M-01", expected_verdict="MEETS_BAR")
    edit_blueprint(pub, "M-01", lambda it: it.update(verdict="MEETS_BAR"))
    edit_card(pub, "M-01", "Verdict **NEEDS_ATTENTION**", "Verdict **MEETS_BAR**")
    _rehash(pub)
    assert _rules(validate_public_dev(pub, SP)) == {"PD006"}


def test_dangerous_win_consistency(pub):
    edit_matrix(pub, "C-08", dangerous_win="NONE")
    edit_blueprint(pub, "C-08", lambda it: it.update(dangerous_win="NONE"))
    edit_card(pub, "C-08", "Dangerous Win CRITICAL", "Dangerous Win NONE")
    _rehash(pub)
    assert _rules(validate_public_dev(pub, SP)) == {"PD007"}


def test_clean_loss_consistency(pub):
    edit_matrix(pub, "K-01", clean_loss="False")
    edit_blueprint(pub, "K-01", lambda it: it.update(clean_loss=False))
    edit_card(pub, "K-01", "Clean Loss True", "Clean Loss False")
    _rehash(pub)
    assert _rules(validate_public_dev(pub, SP)) == {"PD008"}


def test_soft_ptp_cannot_be_positive(pub):
    edit_blueprint(pub, "M-01", lambda it: it["outcome"].update(positive=True))
    edit_card(pub, "M-01", "(positive=False,", "(positive=True,")
    _rehash(pub)
    assert "PD009" in _rules(validate_public_dev(pub, SP))  # AJ-09: PTP_STATED(soft) is not positive


def test_target_code_validity(pub):
    edit_blueprint(pub, "M-01", lambda it: it["findings"][1].update(code="COM-99"))
    edit_card(pub, "M-01", "`COM-05`", "`COM-99`")
    _rehash(pub)
    rep = validate_public_dev(pub, SP)
    assert "PD005" in _rules(rep)
    assert any("COM-05" in i.message and i.rule_id == "PD004" for i in rep.errors)  # secondary target now missing


def test_pair_integrity(pub):
    edit_matrix(pub, "M-01", pair_role="clean")
    edit_card(pub, "M-01", "MP-01 (violating)", "MP-01 (clean)")
    _rehash(pub)
    rep = validate_public_dev(pub, SP)
    assert "PD010" in _rules(rep)


def test_modality_header_and_g7(pub):
    edit_blueprint(pub, "K-07", lambda it: it.update(header=None))
    edit_card(pub, "K-07", " · header `call_start_ts in window (e.g. 18:40 IST)`", "")
    _rehash(pub)
    assert _rules(validate_public_dev(pub, SP)) == {"PD012"}  # G7 PASS needs the header call_start_ts


def test_modality_calling_window(pub):
    edit_blueprint(pub, "K-07", lambda it: it.update(header="call_start_ts in window (e.g. 19:30 IST)"))
    edit_card(pub, "K-07", "(e.g. 18:40 IST)", "(e.g. 19:30 IST)")
    _rehash(pub)
    assert _rules(validate_public_dev(pub, SP)) == {"PD012"}  # outside [08:00, 19:00) but G7 PASS


def test_attribution_derivation(pub):
    edit_blueprint(pub, "C-05", lambda it: it["findings"][1].update(attribution_T="AGENT_BEHAVIOR"))
    edit_card(pub, "C-05", "attribution (T): INDETERMINATE", "attribution (T): AGENT_BEHAVIOR")
    _rehash(pub)
    rep = validate_public_dev(pub, SP)
    assert any(i.rule_id == "PD012" and "UND-03" in i.message for i in rep.errors)  # content_perception in T


def test_missing_beat(pub):
    edit_blueprint(pub, "MD-G5", lambda it: it["findings"][0].update(anchor_beat="B9"))
    edit_card(pub, "MD-G5", "anchor B4", "anchor B9")
    _rehash(pub)
    assert "PD013" in _rules(validate_public_dev(pub, SP))


def test_schema_block_must_equal_schema_file(pub):
    p = pub / DATA_FILES["schema"]
    p.write_text(p.read_text(encoding="utf-8").replace("independent human labeler", "labeler"), encoding="utf-8")
    _rehash(pub)
    assert "PD017" in _rules(validate_public_dev(pub, SP))


# ================================================================================ bench check integration
def _bench_with_design(tmp_path: Path) -> BenchLayout:
    layout = BenchLayout(tmp_path / "bench")
    layout.root.mkdir(parents=True)
    shutil.copytree(PUBLIC, layout.public_dir)
    F.write_registries(layout)
    return layout


def test_dev_items_must_match_the_design(tmp_path):
    layout = _bench_with_design(tmp_path)
    F.add_item(layout, "C-03")                       # language en, but the design says Hinglish (hi-en)
    F.add_item(layout, "ZZ-T01")                     # not a frozen DEV item
    rep = check_bench(layout, SP, scopes=("dev",))
    b041 = {(i.item_id, i.message.split(" ")[0]) for i in rep.issues if i.check_id == "B041"}
    assert ("C-03", "language") in b041 and ("ZZ-T01", "dev") in b041


def test_transcript_identifier_leak_is_an_error(tmp_path):
    layout = _bench_with_design(tmp_path)
    turns = F.STUB_TURNS[:2] + (("AGENT", "stub agent line MP-10"),) + F.STUB_TURNS[3:]
    F.add_item(layout, "C-03", transcript=F.stub_transcript(turns))
    rep = check_bench(layout, SP, scopes=("dev",))
    assert any(i.check_id == "B017" and "MP-10" in i.message for i in rep.issues)


def test_expected_evaluable_item_needs_agent_and_borrower(tmp_path):
    layout = _bench_with_design(tmp_path)
    F.add_item(layout, "K-01", transcript=F.stub_transcript((("AGENT", "stub agent line alpha"),
                                                             ("OTHER", "stub other line beta"))))
    rep = check_bench(layout, SP, scopes=("dev",))
    assert any(i.check_id == "B018" and i.item_id == "K-01" for i in rep.issues)


def test_populated_registry_must_agree_with_the_design(tmp_path):
    layout = _bench_with_design(tmp_path)
    F.write_registries(layout, {"pairs": [{"pair_id": "MP-01", "clean_item": "G-02", "violating_item": "M-01",
                                           "target_check": "COM-05"}],
                                "controls": [{"item_id": "K-01", "target_gates": ["G2"]}]})
    rep = check_bench(layout, SP, scopes=("dev",))
    assert len([i for i in rep.issues if i.check_id == "B043"]) == 2


def test_tuning_only_items_are_never_scored():
    meta = ItemMeta.model_validate({"item_id": "G-02-N5", "split": "dev", "pack": "modality", "language": "hi-en",
                                    "unit_modes": ["A", "A+T"], "artifacts": {"transcript": "t.txt",
                                                                              "audio": "a.wav"},
                                    "tuning_only": True})
    assert meta.scoring_role == "never"
