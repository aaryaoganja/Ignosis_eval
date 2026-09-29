"""Transcript intake — frozen-contract.md §2.3 (R-15). Only labeled plain text (.txt) and JSON (.json).

Plain text:
    # call_start_ts: 2026-09-28T14:05:00+05:30      (optional; ISO 8601 with offset)
    # transcript_provenance: unknown                (optional; platform_live_asr|human|offline_asr|unknown)
    # truncated_start: false                        (optional)
    [00:03.2-00:07.9] AGENT: <text>
    BORROWER: <text>                                (timestamps optional, but all-or-none per file)
Role labels AGENT, BORROWER, OTHER, UNKNOWN; CUSTOMER is an alias of BORROWER. A line without a
recognised label is kept as an UNKNOWN-role turn (so an unlabeled transcript parses and is later
NOT_EVALUABLE with ROLE_UNCERTAIN: DC-00 finds no AGENT turn, and §8 requires labels in TRANSCRIPT mode).
Reliability markers `[inaudible]`, `[crosstalk]`, `???` make their span (turn) unreliable. Any other file format
is rejected (SRT/VTT deferred).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from ignosis_eval.contracts.canonical_input import TranscriptHeader
from ignosis_eval.contracts.enums import Role, TranscriptProvenance

RELIABILITY_MARKERS = ("[inaudible]", "[crosstalk]", "???")
ROLE_LABELS = {"AGENT": Role.AGENT, "BORROWER": Role.BORROWER, "CUSTOMER": Role.BORROWER, "OTHER": Role.OTHER,
               "UNKNOWN": Role.UNKNOWN}
HEADER_KEYS = ("call_start_ts", "transcript_provenance", "truncated_start")
_TS = r"\d+(?::\d{2}){1,2}(?:\.\d+)?"
_LINE = re.compile(rf"^(?:\[(?P<s>{_TS})-(?P<e>{_TS})\]\s*)?(?:(?P<label>[A-Z]+):\s?)?(?P<text>.*)$")
_HEADER = re.compile(r"^#\s*(?P<key>[a-z_]+)\s*:\s*(?P<value>.*?)\s*$")


class TranscriptParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedTurn:
    role: Role
    text: str
    start_s: float | None
    end_s: float | None
    unreliable: bool


@dataclass(frozen=True)
class ParsedTranscript:
    header: TranscriptHeader
    turns: tuple[ParsedTurn, ...]
    has_timestamps: bool


def has_reliability_marker(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in RELIABILITY_MARKERS)


def _seconds(ts: str) -> float:
    parts = [float(p) for p in ts.split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def _header(raw: dict[str, object]) -> TranscriptHeader:
    unknown = set(raw) - set(HEADER_KEYS)
    if unknown:
        raise TranscriptParseError(f"unknown header keys {sorted(unknown)}")
    values: dict[str, object] = {}
    ts = raw.get("call_start_ts")
    if ts not in (None, "", "null"):
        try:
            parsed = datetime.fromisoformat(str(ts))
        except ValueError as exc:
            raise TranscriptParseError(f"call_start_ts is not ISO 8601: {ts!r}") from exc
        if parsed.tzinfo is None:
            raise TranscriptParseError("call_start_ts must carry a UTC offset")
        values["call_start_ts"] = parsed
    prov = raw.get("transcript_provenance")
    if prov not in (None, "", "null"):
        if prov not in {p.value for p in TranscriptProvenance}:
            raise TranscriptParseError(f"transcript_provenance {prov!r} not in {[p.value for p in TranscriptProvenance]}")
        values["transcript_provenance"] = prov
    trunc = raw.get("truncated_start")
    if trunc not in (None, "", "null"):
        if isinstance(trunc, bool):
            values["truncated_start"] = trunc
        elif str(trunc).lower() in ("true", "false"):
            values["truncated_start"] = str(trunc).lower() == "true"
        else:
            raise TranscriptParseError(f"truncated_start must be true or false, got {trunc!r}")
    try:
        return TranscriptHeader.model_validate(values)
    except ValidationError as exc:
        raise TranscriptParseError(str(exc)) from exc


def _finish(header: TranscriptHeader, turns: list[ParsedTurn]) -> ParsedTranscript:
    timed = [t.start_s is not None for t in turns]
    if turns and any(timed) and not all(timed):
        raise TranscriptParseError("timestamps must be all-or-none per file (§2.3)")
    for t in turns:
        if t.start_s is not None and t.end_s is not None and t.start_s > t.end_s:
            raise TranscriptParseError(f"turn timestamp start > end: {t}")
    return ParsedTranscript(header, tuple(turns), bool(turns) and all(timed))


def parse_txt(content: str) -> ParsedTranscript:
    header_raw: dict[str, object] = {}
    turns: list[ParsedTurn] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        hm = _HEADER.match(line)
        if hm:
            if turns:
                raise TranscriptParseError(f"line {lineno}: header lines must precede all turns")
            key = hm.group("key")
            if key in header_raw:
                raise TranscriptParseError(f"line {lineno}: duplicate header {key}")
            header_raw[key] = hm.group("value")
            continue
        m = _LINE.match(line)
        assert m is not None  # the pattern matches any line
        label, text = m.group("label"), m.group("text")
        if label is not None and label not in ROLE_LABELS:  # not a role label: keep the whole line as text
            text, role = line[m.start("label"):], Role.UNKNOWN
        else:
            role = ROLE_LABELS[label] if label else Role.UNKNOWN
        start = _seconds(m.group("s")) if m.group("s") else None
        end = _seconds(m.group("e")) if m.group("e") else None
        text = text.strip()
        turns.append(ParsedTurn(role, text, start, end, has_reliability_marker(text)))
    return _finish(_header(header_raw), turns)


def parse_json(content: str) -> ParsedTranscript:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise TranscriptParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or "turns" not in data:
        raise TranscriptParseError("JSON transcript must be an object with `turns`")
    if set(data) - {"header", "turns"}:
        raise TranscriptParseError(f"unknown top-level keys {sorted(set(data) - {'header', 'turns'})}")
    header = _header(data.get("header") or {})
    turns: list[ParsedTurn] = []
    for i, t in enumerate(data["turns"], start=1):
        if not isinstance(t, dict) or set(t) - {"speaker", "text", "start_s", "end_s"}:
            raise TranscriptParseError(f"turn {i}: expected keys speaker, text, start_s, end_s")
        spk = t.get("speaker")
        role = ROLE_LABELS.get(str(spk).upper(), Role.UNKNOWN) if spk is not None else Role.UNKNOWN
        text = str(t.get("text", ""))
        start, end = t.get("start_s"), t.get("end_s")
        turns.append(ParsedTurn(role, text, None if start is None else float(start),
                                None if end is None else float(end), has_reliability_marker(text)))
    return _finish(header, turns)


def parse_transcript(path: str | Path) -> ParsedTranscript:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".txt":
        return parse_txt(p.read_text(encoding="utf-8"))
    if suffix == ".json":
        return parse_json(p.read_text(encoding="utf-8"))
    raise TranscriptParseError(f"unsupported transcript format {suffix!r}: only .txt and .json (R-15)")
