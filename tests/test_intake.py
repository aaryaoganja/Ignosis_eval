"""Transcript intake — frozen-contract §2.3 (R-15)."""

from __future__ import annotations

import json

import pytest

from ignosis_eval.contracts.enums import Role
from ignosis_eval.pipeline.intake import TranscriptParseError, parse_json, parse_transcript, parse_txt


def test_txt_with_header_and_timestamps():
    p = parse_txt("# call_start_ts: 2026-09-28T14:05:00+05:30\n# transcript_provenance: human\n"
                  "[00:03.2-00:07.9] AGENT: stub a\n[00:08.1-00:09.0] CUSTOMER: stub b [inaudible]\n")
    assert p.header.call_start_ts is not None and p.header.transcript_provenance.value == "human"
    assert [t.role for t in p.turns] == [Role.AGENT, Role.BORROWER]  # CUSTOMER alias
    assert p.has_timestamps and p.turns[1].unreliable and not p.turns[0].unreliable


def test_timestamps_all_or_none():
    with pytest.raises(TranscriptParseError):
        parse_txt("[00:01-00:02] AGENT: a\nBORROWER: b\n")


def test_header_rules():
    with pytest.raises(TranscriptParseError):
        parse_txt("# call_start_ts: 2026-09-28T14:05:00\nAGENT: a\n")  # no offset
    with pytest.raises(TranscriptParseError):
        parse_txt("# unknown_key: 1\nAGENT: a\n")
    with pytest.raises(TranscriptParseError):
        parse_txt("AGENT: a\n# truncated_start: true\n")
    assert parse_txt("# truncated_start: true\nAGENT: a\n").header.truncated_start


def test_unlabeled_lines_are_unknown_role():
    p = parse_txt("stub line without label\nNOTE: stub\n")
    assert [t.role for t in p.turns] == [Role.UNKNOWN, Role.UNKNOWN]
    assert p.turns[1].text == "NOTE: stub"


def test_json_format_and_rejections(tmp_path):
    p = parse_json(json.dumps({"header": {"call_start_ts": None, "transcript_provenance": "unknown",
                                          "truncated_start": False},
                               "turns": [{"speaker": "agent", "text": "stub", "start_s": 1.0, "end_s": 2.0}]}))
    assert p.turns[0].role is Role.AGENT and p.has_timestamps
    with pytest.raises(TranscriptParseError):
        parse_json(json.dumps({"turns": [], "extra": 1}))
    srt = tmp_path / "x.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nstub\n")
    with pytest.raises(TranscriptParseError):
        parse_transcript(srt)
