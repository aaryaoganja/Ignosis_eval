"""Transcript QC (benchmark/transcript_qc.py, TQxxx) against the frozen DEV design in bench/public.

Every transcript here is an obviously synthetic stub ("stub agent line ...") written to a temporary directory under a
DEV item's file name; no benchmark wording is authored and bench/public is only read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import factories as F
from ignosis_eval.benchmark.public_dev import parse_case_cards
from ignosis_eval.benchmark.transcript_qc import (
    card_copy_source,
    card_is_snippet,
    card_requires_seed_check,
    card_word_bans,
    changed_turns,
    check_transcripts,
    prohibited_lexicon,
    term_pattern,
)
from ignosis_eval.cli import main
from ignosis_eval.pipeline.intake import parse_txt

PUBLIC = F.REPO / "bench" / "public"
SP = F.spec()
IN_WINDOW = "2026-01-05T11:00:00+05:30"

BASE = [("AGENT", "stub agent line one"), ("BORROWER", "stub borrower line two"),
        ("AGENT", "stub agent line three"), ("BORROWER", "stub borrower line four"),
        ("AGENT", "stub agent line five"), ("BORROWER", "stub borrower line six"),
        ("AGENT", "stub agent line seven"), ("BORROWER", "stub borrower line eight")]


def _write(d: Path, item_id: str, turns, header: str | None = None, suffix: str = ".txt") -> Path:
    lines = [f"# call_start_ts: {header}"] if header else []
    lines += [f"{r}: {t}" if r else t for r, t in turns]
    p = d / f"{item_id}{suffix}"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _edit(turns, n: int, text: str):
    out = list(turns)
    out[n - 1] = (out[n - 1][0], text)
    return out


def _qc(d: Path, **kw):
    return check_transcripts(d, SP, PUBLIC, **kw)


def _ids(rep, item_id: str | None = None) -> list[str]:
    return sorted(i.check_id for i in rep.issues if item_id is None or i.item_id == item_id)


@pytest.fixture
def batch(tmp_path: Path) -> Path:
    """A clean MP-04 pair: one agent turn edited, equal length, no header (the design has none)."""
    _write(tmp_path, "K-01", BASE)
    _write(tmp_path, "C-01", _edit(BASE, 5, "stub agent line five edited"))
    return tmp_path


def test_clean_pair_has_no_issue(batch):
    rep = _qc(batch)
    assert rep.issues == [] and rep.ok
    assert sorted(rep.checked) == ["C-01", "K-01"] and rep.pairs_checked == ["MP-04"]


def test_items_filter_and_incomplete_pair(batch):
    rep = _qc(batch, items=["K-01"])
    assert list(rep.checked) == ["K-01"] and _ids(rep) == ["TQ015"] and rep.ok


def test_file_names_and_parse_errors(batch):
    _write(batch, "Z-99", BASE)
    _write(batch, "K-01", BASE, suffix=".json")  # a second transcript for the same item
    (batch / "C-03.txt").write_text("AGENT: stub\n# call_start_ts: 2026-01-05T11:00:00+05:30\n", encoding="utf-8")
    (batch / "C-05.txt").write_text("[00:01.0-00:02.0] AGENT: stub\nBORROWER: stub\n", encoding="utf-8")
    rep = _qc(batch)
    assert sorted((i.check_id, i.item_id) for i in rep.errors) == [
        ("TQ001", "K-01"), ("TQ001", "Z-99"), ("TQ002", "C-03"), ("TQ002", "C-05")]
    assert "K-01" not in rep.checked and _ids(rep, "C-01") == ["TQ015"]  # K-01 left unchecked, so MP-04 is incomplete


def test_roles(batch):
    _write(batch, "C-01", _edit(BASE, 6, "stub borrower line six") + [("OTHER", "stub other speaker")])
    _write(batch, "K-01", [("AGENT", "stub"), ("", "stub unlabeled line")])
    rep = _qc(batch)
    assert _ids(rep, "C-01") == ["TQ003"]
    assert _ids(rep, "K-01") == ["TQ003", "TQ003"]  # the UNKNOWN turn, and no BORROWER turn


@pytest.mark.parametrize("text", ["stub mentions C-08", "stub mentions MP-10", "stub mentions UND-01",
                                  "stub mentions holdout", "stub mentions SYS-2", "stub mentions k-01.txt"])
def test_p17_identity(batch, text):
    _write(batch, "K-01", _edit(BASE, 3, text))
    rep = _qc(batch)
    assert "TQ004" in _ids(rep, "K-01")


def test_p17_ordinary_words_pass(batch):
    _write(batch, "K-01", _edit(BASE, 3, "stub language email E-mail X-ray dev core micro reference KF-58213"))
    assert "TQ004" not in _ids(_qc(batch), "K-01")


def test_header_presence(batch):
    _write(batch, "K-01", BASE, header=IN_WINDOW)  # the design has no header for K-01
    _write(batch, "G-02", BASE)                    # and one for G-02
    rep = _qc(batch)
    assert _ids(rep, "K-01") == ["TQ005"] and _ids(rep, "G-02") == ["TQ005", "TQ015"]  # M-01 not in the batch


@pytest.mark.parametrize(("ts", "ok"), [
    ("2026-01-05T08:00:00+05:30", True), ("2026-01-05T18:59:00+05:30", True),
    ("2026-01-05T19:00:00+05:30", False), ("2026-01-05T07:59:00+05:30", False),
    ("2026-01-05T13:29:00+00:00", True), ("2026-01-05T13:30:00+00:00", False),  # 18:59 / 19:00 IST
])
def test_header_calling_window(tmp_path, ts, ok):
    _write(tmp_path, "G-02", BASE, header=ts)
    rep = _qc(tmp_path)
    assert ("TQ006" in _ids(rep, "G-02")) is not ok


def test_markers_and_truncation(batch):
    (batch / "K-01.txt").write_text("# truncated_start: true\n" + "\n".join(
        f"{r}: {t}" for r, t in _edit(BASE, 2, "stub [inaudible] line")) + "\n", encoding="utf-8")
    rep = _qc(batch)
    assert _ids(rep, "K-01") == ["TQ007", "TQ008"] and rep.ok  # warnings only


def test_monologue(batch):
    _write(batch, "K-01", _edit(BASE, 3, " ".join(["stub"] * 80)))
    assert "TQ009" not in _ids(_qc(batch), "K-01")
    _write(batch, "K-01", _edit(BASE, 3, " ".join(["stub"] * 81)))
    assert "TQ009" in _ids(_qc(batch), "K-01")
    _write(batch, "K-01", _edit(BASE, 2, " ".join(["stub"] * 200)))  # a borrower turn is not a monologue
    assert "TQ009" not in _ids(_qc(batch), "K-01")
    timed = "\n".join(f"[00:{2 * i:02d}.0-00:{2 * i + 1:02d}.0] {r}: {t}" for i, (r, t) in enumerate(BASE[:4]))
    (batch / "K-01.txt").write_text(timed + "\n[00:10.0-00:41.0] AGENT: stub short timed turn\n", encoding="utf-8")
    assert "TQ009" in _ids(_qc(batch), "K-01")  # duration basis: 31 s > 30 s


def test_frozen_card_rules_parse():
    cards = parse_case_cards((PUBLIC / "dev-case-cards.md").read_text(encoding="utf-8"))
    bans = {iid: sorted((b.speaker, b.term) for b in card_word_bans(c)) for iid, c in cards.items()}
    assert {k: v for k, v in bans.items() if v} == {
        "K-01": [("AGENT", "EMI"), ("AGENT", "dues"), ("AGENT", "loan"), ("AGENT", "payment")],
        "K-07": [("BORROWER", "settlement")],
        "C-03": [("ANY", "AI disclosure")],
    }
    assert sorted(i for i, c in cards.items() if card_requires_seed_check(c)) == ["C-05"]
    assert sorted(i for i, c in cards.items() if card_is_snippet(c)) == [f"SN-D0{n}" for n in range(1, 7)]
    assert {i: card_copy_source(c) for i, c in cards.items() if card_copy_source(c)} == {"G-02-N5": "G-02"}
    lex = prohibited_lexicon(SP)
    assert {"police", "FIR", "manager ko batayenge"} <= set(lex)


def test_card_word_bans(batch):
    _write(batch, "K-01", _edit(BASE, 3, "stub says Loan here"))
    _write(batch, "C-01", _edit(_edit(BASE, 5, "stub agent line five edited"), 4, "stub says loan here"))
    rep = _qc(batch)
    assert _ids(rep, "K-01") == ["TQ010"]              # agent-only ban, any case
    assert "TQ010" not in _ids(rep, "C-01")            # C-01 has no ban; a borrower turn would not count anyway
    _write(batch, "K-07", _edit(BASE, 2, "stub says settlement"), header=IN_WINDOW)
    _write(batch, "C-08", _edit(BASE, 3, "stub says settlement"))
    rep = _qc(batch)
    assert "TQ010" in _ids(rep, "K-07") and "TQ010" not in _ids(rep, "C-08")
    _write(batch, "C-03", _edit(BASE, 3, "stub says ai disclosure"))
    assert "TQ010" in _ids(_qc(batch), "C-03")


def test_lexicon_seed_rule(tmp_path):
    _write(tmp_path, "C-05", _edit(BASE, 3, "stub says fir later"))  # Hinglish "fir" is not the acronym FIR
    assert "TQ011" not in _ids(_qc(tmp_path), "C-05")
    for text in ("stub says FIR", "stub says Police", "stub says manager ko batayenge"):
        _write(tmp_path, "C-05", _edit(BASE, 3, text))
        assert "TQ011" in _ids(_qc(tmp_path), "C-05"), text
    _write(tmp_path, "C-10", _edit(BASE, 3, "stub says police"))  # only C-05's card carries the rule
    assert "TQ011" not in _ids(_qc(tmp_path), "C-10")
    assert term_pattern("police").search("POLICE") and not term_pattern("FIR", acronym_case=True).search("fir")


def test_pair_length(batch):
    _write(batch, "C-01", _edit(BASE, 5, "stub agent line five edited with many more extra words added"))
    rep = _qc(batch)
    assert _ids(rep) == ["TQ012"] and rep.ok


def test_pair_agent_edit_count(batch):
    _write(batch, "C-01", BASE)
    assert _ids(_qc(batch)) == ["TQ013"]  # identical members: nothing edited
    four = BASE
    for n in (1, 3, 5, 7):
        four = _edit(four, n, BASE[n - 1][1] + " x")
    _write(batch, "C-01", four)
    assert "TQ013" in _ids(_qc(batch))
    three = BASE
    for n in (3, 5, 7):
        three = _edit(three, n, BASE[n - 1][1] + " x")
    _write(batch, "C-01", three)
    assert "TQ013" not in _ids(_qc(batch))


def test_pair_customer_side_changes(batch):
    reacting = _edit(_edit(BASE, 5, "stub agent line five edited"), 6, "stub borrower line six reacts")
    _write(batch, "C-01", reacting)
    assert _ids(_qc(batch)) == []
    _write(batch, "C-01", _edit(reacting, 2, "stub borrower line two changed"))
    assert _ids(_qc(batch)) == ["TQ014"]


def test_pair_declared_incidental_borrower_context(tmp_path):
    """MP-01 declares one incidental borrower-context difference (BD-03): one unexplained change is allowed."""
    clean = BASE
    viol = _edit(_edit(BASE, 2, "stub borrower context differs"), 5, "stub agent line five edited")
    _write(tmp_path, "G-02", clean, header=IN_WINDOW)
    _write(tmp_path, "M-01", viol)
    assert _ids(_qc(tmp_path)) == []
    _write(tmp_path, "M-01", _edit(viol, 4, "stub borrower line four changed"))
    assert _ids(_qc(tmp_path)) == ["TQ014"]


def test_changed_turns_alignment():
    a = parse_txt("AGENT: s1\nBORROWER: s2\nAGENT: s3\nBORROWER: s4\nAGENT: s5\n")
    b = parse_txt("AGENT: s1\nBORROWER: s2\nAGENT: x3\nAGENT: s5\n")
    assert changed_turns(a, b) == ([3, 4], [3])


def test_cli(batch, capsys):
    args = ["bench", "transcript-qc", str(batch), "--bench-root", str(F.REPO / "bench"), "--spec-dir", str(F.SPEC_DIR)]
    assert main(args) == 0
    assert "pairs checked: ['MP-04']" in capsys.readouterr().out
    _write(batch, "K-01", _edit(BASE, 3, "stub mentions MP-04"))
    assert main(args) == 1


def test_snippet_shape(tmp_path):
    _write(tmp_path, "SN-D01", [("BORROWER", "stub utterance")])
    assert _ids(_qc(tmp_path)) == []
    _write(tmp_path, "SN-D01", [("BORROWER", "stub utterance"), ("BORROWER", "stub second")])
    assert _ids(_qc(tmp_path)) == ["TQ016"]
    _write(tmp_path, "SN-D01", [("AGENT", "stub utterance")])
    assert _ids(_qc(tmp_path)) == ["TQ016"]


def test_derived_copy(tmp_path):
    _write(tmp_path, "G-02", BASE, header=IN_WINDOW)
    _write(tmp_path, "G-02-N5", BASE)  # the copy carries no header (the design has none for it)
    assert _ids(_qc(tmp_path), "G-02-N5") == []
    _write(tmp_path, "G-02-N5", _edit(BASE, 3, "stub agent line three changed"))
    assert _ids(_qc(tmp_path), "G-02-N5") == ["TQ017"] and not _qc(tmp_path).ok
    (tmp_path / "G-02.txt").unlink()
    rep = _qc(tmp_path)
    assert _ids(rep, "G-02-N5") == ["TQ017"] and rep.ok  # source absent: a warning only


def test_committed_dev_drafts():
    """bench/dev/transcripts holds exactly the frozen DEV design items, passes the transcript QC with no issue, and
    records every item's authoring provenance (CC009: an LLM-assisted item names the model family)."""
    import yaml

    from ignosis_eval.benchmark.public_dev import validate_public_dev

    drafts = F.REPO / "bench" / "dev" / "transcripts"
    design = validate_public_dev(PUBLIC, SP).design
    assert design is not None
    assert {p.stem for p in drafts.glob("*.txt")} == set(design.items)
    rep = _qc(drafts)
    assert rep.issues == [] and rep.pairs_checked == ["MP-01", "MP-04", "MP-10"]
    prov = yaml.safe_load((drafts / "provenance.yaml").read_text(encoding="utf-8"))
    entries = {e["item_id"]: e for e in prov["items"]}
    assert set(entries) == set(design.items) and prov["status"] == "draft"
    assert all(e["llm_family"] for e in entries.values() if e["llm_assisted"])
    assert all("reference_date" in entries[i] for i in design.items if i.startswith("SN-"))
    # still drafts: nobody has signed a transcript off (the review checklist is for the human editor)
    assert prov["human_review_pending"] is True and all(e["human_review_pending"] for e in entries.values())
    checklist = (drafts / "REVIEW-CHECKLIST.md").read_text(encoding="utf-8")
    assert all(f"| {i} |" in checklist for i in design.items)
