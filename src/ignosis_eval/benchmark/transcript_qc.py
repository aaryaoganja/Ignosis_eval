"""Transcript QC for DEV transcripts at authoring time, before the transcript hash freeze. Never mutates anything.

Input: a directory of draft transcripts named `<ITEM_ID>.txt` / `.json` (e.g. an author's hand-off batch). They are
checked against the frozen DEV design in bench/public (verified first by `validate_public_dev`), the frozen contract
and the profile. Every check is deterministic and has a frozen basis. Semantic QC (beats, gate behavior, verdict
consistency, naturalness) stays with the human reviewer.

  TQ001 error    the file name is not a frozen DEV design item id, or several files name the same item (unchecked)
  TQ002 error    the transcript does not parse (frozen-contract §2.3)
  TQ003 error    roles: an OTHER / UNKNOWN turn, or no AGENT / no BORROWER turn, in an item the design expects
                 EVALUABLE (DC-00 / DC-02, SC-03; a customer-side third party is labeled BORROWER)
  TQ004 error    P-17 identity in turn text or header values: item / pair / check ids, benchmark labels, or the
                 item's own id and file name (SC-05; the runner's pre-run test would fail the run)
  TQ005 error    `call_start_ts` present / absent against the design (authoring constraint 7; BD-04)
  TQ006 error    where the design has a header: the header's G7 result (the front end's own precheck_g7, calling
                 window [start, end) in the profile timezone) differs from the design's G7 (BD-02: no G7 positive)
  TQ007 warning  a reliability marker `[inaudible]`, `[crosstalk]` or `???` makes its turn unreliable (§2.3, DC-01)
  TQ008 warning  `truncated_start: true` (DC-TRUNC: checks near the start become INCONCLUSIVE; constraint 6)
  TQ009 error    an agent turn exceeds the TRT-06 monologue threshold (duration if timestamped, else words; AJ-02)
                 while the design expects no TRT-06 finding
  TQ010 error    a term the item's frozen case card bans appears in the banned speaker's turns
                 ("must not let the agent say '...'", "Borrower never says '...'", "no rubric words ('...')")
  TQ011 error    a prohibited-consequence lexicon phrase (profile `terms` and `seed_candidates_unreviewed`) appears
                 in an item whose card says "No words from the prohibited lexicon seeds"
  TQ012 warning  pair lengths differ by more than 10% (authoring constraint 4; words, as B013)
  TQ013 error    pair: the number of agent turns that differ between the members is not 1–3 (constraint 4)
  TQ014 warning  pair: a changed customer-side turn does not follow a changed agent turn, beyond the declared
                 incidental borrower-context differences (constraint 4 lets only reacting borrower turns change; BD-03)
  TQ015 warning  pair: only one member is in the batch, so the pair checks were skipped

Seeds are used here as authoring QC only: the profile forbids them in benchmark runs (B-04), and the card that
invokes them names them. Terms match on word boundaries ignoring case, except all-caps lexicon seeds (acronyms such
as FIR, which would otherwise hit the Hinglish word "fir"), which match their case only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from ignosis_eval.benchmark.public_dev import (
    DATA_FILES,
    PAIR_INCIDENTAL,
    DesignCard,
    DesignItem,
    DevDesign,
    PublicDevReport,
    parse_case_cards,
    validate_public_dev,
)
from ignosis_eval.contracts.canonical_input import TranscriptHeader
from ignosis_eval.contracts.enums import GateStatus, InputMode, Role
from ignosis_eval.contracts.unit_alias import payload_violations
from ignosis_eval.pipeline.intake import ParsedTranscript, TranscriptParseError, parse_transcript
from ignosis_eval.pipeline.prechecks import precheck_g7
from ignosis_eval.spec.loader import Spec

TRANSCRIPT_SUFFIXES = (".txt", ".json")
Speaker = Literal["AGENT", "BORROWER", "ANY"]

_TERMS = r"(?P<terms>'[^']+'(?:(?:,| or| and)\s*'[^']+')*)"
BAN_PATTERNS: tuple[tuple[re.Pattern[str], Speaker], ...] = (
    (re.compile(r"must not let the agent say " + _TERMS, re.IGNORECASE), "AGENT"),
    (re.compile(r"Borrower never says " + _TERMS, re.IGNORECASE), "BORROWER"),
    (re.compile(r"no rubric words \(" + _TERMS + r"\)", re.IGNORECASE), "ANY"),
)
LEXICON_SEED_RULE = "No words from the prohibited lexicon seeds"


@dataclass(frozen=True)
class QcIssue:
    check_id: str
    severity: Literal["error", "warning"]
    message: str
    item_id: str | None = None

    def __str__(self) -> str:
        who = f"[{self.item_id}] " if self.item_id else ""
        return f"{self.severity.upper()} {self.check_id} {who}{self.message}"


@dataclass(frozen=True)
class WordBan:
    speaker: Speaker
    term: str
    source: str  # the card clause it came from


@dataclass
class TranscriptQcReport:
    issues: list[QcIssue] = field(default_factory=list)
    checked: dict[str, Path] = field(default_factory=dict)  # item id -> transcript file
    pairs_checked: list[str] = field(default_factory=list)
    public_dev: PublicDevReport | None = None

    @property
    def errors(self) -> list[QcIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def item_issues(self, item_id: str) -> list[QcIssue]:
        return [i for i in self.issues if i.item_id == item_id]


# ------------------------------------------------------------------------------------------ card-derived rules
def card_word_bans(card: DesignCard) -> list[WordBan]:
    """Quoted terms the frozen card's authoring notes forbid, with the speaker they apply to."""
    notes = card.fields.get("Authoring notes", "")
    out: list[WordBan] = []
    for pattern, speaker in BAN_PATTERNS:
        for m in pattern.finditer(notes):
            out += [WordBan(speaker, t, m.group(0)) for t in re.findall(r"'([^']+)'", m["terms"])]
    return out


def card_requires_seed_check(card: DesignCard) -> bool:
    return LEXICON_SEED_RULE.lower() in card.fields.get("Authoring notes", "").lower()


def prohibited_lexicon(spec: Spec) -> list[str]:
    """Every prohibited-consequence phrase in the profile: reviewed `terms` plus `seed_candidates_unreviewed`."""
    out: list[str] = []
    for cat in spec.profile["lexicons"]["prohibited_consequences"].values():
        out += [str(t) for t in (cat.get("terms") or [])] + [str(t) for t in (cat.get("seed_candidates_unreviewed")
                                                                                 or [])]
    return sorted(set(out))


def term_pattern(term: str, *, acronym_case: bool = False) -> re.Pattern[str]:
    """Word-bounded match ignoring case; with `acronym_case`, an all-caps term (FIR) matches its case only."""
    flags = 0 if acronym_case and term.isupper() else re.IGNORECASE
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", flags)


def _speaks(role: Role, speaker: Speaker) -> bool:
    return speaker == "ANY" or role.value == speaker


# ------------------------------------------------------------------------------------------ per-item checks
def _free_text(parsed: ParsedTranscript) -> list[tuple[str, str]]:
    """(where, text) for every evaluator-bound free-text string: turn texts and header values."""
    out = [(f"turn {n}", t.text) for n, t in enumerate(parsed.turns, start=1)]
    h: TranscriptHeader = parsed.header
    if h.call_start_ts is not None:
        out.append(("header call_start_ts", h.call_start_ts.isoformat()))
    return out


def check_item(item_id: str, path: Path, parsed: ParsedTranscript, design: DesignItem, card: DesignCard | None,
               spec: Spec) -> list[QcIssue]:
    out: list[QcIssue] = []

    def add(check: str, severity: Literal["error", "warning"], msg: str) -> None:
        out.append(QcIssue(check, severity, msg, item_id))

    turns = parsed.turns
    # TQ003 roles
    if design.evaluability == "EVALUABLE":
        odd = [n for n, t in enumerate(turns, start=1) if t.role in (Role.OTHER, Role.UNKNOWN)]
        if odd:
            add("TQ003", "error", f"turns {odd} are OTHER/UNKNOWN in an item expected EVALUABLE: label the "
                                  "customer-side speaker BORROWER, also a third party (rubric event_common.turn, "
                                  "SC-03); an unlabeled line parses as UNKNOWN")
        roles = {t.role for t in turns}
        if not {Role.AGENT, Role.BORROWER} <= roles:
            add("TQ003", "error", f"roles {sorted(r.value for r in roles)}: an EVALUABLE item needs an AGENT and a "
                                  "BORROWER turn (DC-00 / DC-02)")
    # TQ004 P-17 identity
    own = [item_id, path.name, path.stem]
    for where, text in _free_text(parsed):
        leaks = payload_violations(text, free_text=True, own_tokens=own)
        if leaks:
            add("TQ004", "error", f"{where}: {sorted(set(leaks))} (P-17 / SC-05: evaluators would see benchmark "
                                  "identity and the pre-run test fails the run)")
    # TQ005 / TQ006 header and calling window
    has_header = parsed.header.call_start_ts is not None
    if has_header != design.has_header:
        add("TQ005", "error", f"call_start_ts header {'present' if has_header else 'absent'} but the frozen design "
                              f"says {'present' if design.has_header else 'absent'} (constraint 7; BD-04)")
    ts = parsed.header.call_start_ts
    if ts is not None and design.has_header and design.gates is not None:  # a surplus header is TQ005's
        g7 = precheck_g7(parsed.header, InputMode.TRANSCRIPT, spec).status
        want = design.gates.get("G7")
        if g7.value != want:
            win = spec.profile["calling_window"]
            add("TQ006", "error", f"call_start_ts {ts.isoformat()} gives G7 {g7.value}, the design expects {want} "
                                  f"(window [{win['start']}, {win['end']}) {win['timezone']}; BD-02: no G7 "
                                  "positive)")
        elif g7 is GateStatus.FAIL:
            add("TQ006", "error", "the header is outside the calling window: BD-02 states that bench-a1 contains "
                                  "no G7 positive")
    # TQ007 / TQ008
    marked = [n for n, t in enumerate(turns, start=1) if t.unreliable]
    if marked:
        add("TQ007", "warning", f"turns {marked} carry a reliability marker, so their spans are unreliable "
                                "(§2.3, DC-01): make sure no required evidence sits in them")
    if parsed.header.truncated_start:
        add("TQ008", "warning", "truncated_start: true (DC-TRUNC); no DEV core item tests truncation")
    # TQ009 monologue
    if "TRT-06" not in {f.code for f in design.findings}:
        max_s, max_w = float(spec.threshold("monologue_max_seconds")), int(spec.threshold("monologue_max_words"))
        for n, t in enumerate(turns, start=1):
            if t.role is not Role.AGENT:
                continue
            if t.start_s is not None and t.end_s is not None:
                if t.end_s - t.start_s > max_s:
                    add("TQ009", "error", f"agent turn {n} lasts {t.end_s - t.start_s:.1f} s > {max_s:g} s: an "
                                          "unintended TRT-06 finding (AJ-02)")
            elif len(t.text.split()) > max_w:
                add("TQ009", "error", f"agent turn {n} has {len(t.text.split())} words > {max_w}: an unintended "
                                      "TRT-06 finding (AJ-02)")
    # TQ010 / TQ011 card rules
    if card is not None:
        for ban in card_word_bans(card):
            pat = term_pattern(ban.term)
            hits = [n for n, t in enumerate(turns, start=1) if _speaks(t.role, ban.speaker) and pat.search(t.text)]
            if hits:
                who = "any" if ban.speaker == "ANY" else ban.speaker
                add("TQ010", "error", f"'{ban.term}' in {who} turns {hits}; the frozen card says: {ban.source!r}")
        if card_requires_seed_check(card):
            for term in prohibited_lexicon(spec):
                pat = term_pattern(term, acronym_case=True)
                hits = [n for n, t in enumerate(turns, start=1) if pat.search(t.text)]
                if hits:
                    add("TQ011", "error", f"prohibited-lexicon phrase '{term}' in turns {hits}; the frozen card "
                                          f"says: {LEXICON_SEED_RULE!r}")
    return out


# ------------------------------------------------------------------------------------------ pair checks
def _words(parsed: ParsedTranscript) -> int:
    return sum(len(t.text.split()) for t in parsed.turns)


def changed_turns(clean: ParsedTranscript, viol: ParsedTranscript) -> tuple[list[int], list[int]]:
    """1-based turn numbers outside the identical aligned blocks, per member (exact role + text equality)."""
    a = [(t.role, t.text) for t in clean.turns]
    b = [(t.role, t.text) for t in viol.turns]
    ca: list[int] = []
    cb: list[int] = []
    for op, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op != "equal":
            ca += range(i1 + 1, i2 + 1)
            cb += range(j1 + 1, j2 + 1)
    return ca, cb


def check_pair(pid: str, clean_id: str, clean: ParsedTranscript, viol_id: str, viol: ParsedTranscript) -> list[QcIssue]:
    out: list[QcIssue] = []
    wa, wb = _words(clean), _words(viol)
    if abs(wa - wb) > 0.10 * max(wa, wb):
        out.append(QcIssue("TQ012", "warning", f"pair {pid}: {clean_id} {wa} words vs {viol_id} {wb} words differ by "
                                               "more than 10% (constraint 4)"))
    ca, cb = changed_turns(clean, viol)
    agent_a = [n for n in ca if clean.turns[n - 1].role is Role.AGENT]
    agent_b = [n for n in cb if viol.turns[n - 1].role is Role.AGENT]
    edited = max(len(agent_a), len(agent_b))
    if not 1 <= edited <= 3:
        out.append(QcIssue("TQ013", "error", f"pair {pid}: {edited} agent turns differ ({clean_id} {agent_a}, "
                                             f"{viol_id} {agent_b}); constraint 4 edits 1–3 agent turns"))
    allowed = sum(1 for d in PAIR_INCIDENTAL.get(pid, ()) if d.aspect == "borrower_context")
    unexplained = [n for n in cb if viol.turns[n - 1].role is not Role.AGENT
                   and not (n >= 2 and (n - 1) in cb and viol.turns[n - 2].role is Role.AGENT)]
    if len(unexplained) > allowed:
        declared = [f"{d.where} ({d.basis})" for d in PAIR_INCIDENTAL.get(pid, ()) if d.aspect == "borrower_context"]
        out.append(QcIssue("TQ014", "warning", f"pair {pid}: {viol_id} customer-side turns {unexplained} changed "
                                               "without following a changed agent turn; declared incidental "
                                               f"borrower-context differences: {declared or 'none'} (constraint 4)"))
    return out


# ------------------------------------------------------------------------------------------ driver
def discover_transcripts(directory: Path) -> tuple[dict[str, Path], list[QcIssue]]:
    """<ITEM_ID> -> transcript file. An item with two transcript files is reported and left unchecked."""
    by_item: dict[str, list[Path]] = {}
    for p in sorted(directory.iterdir()):
        if p.is_file() and p.suffix.lower() in TRANSCRIPT_SUFFIXES:
            by_item.setdefault(p.stem, []).append(p)
    issues = [QcIssue("TQ001", "error", f"several transcripts for one item: {[p.name for p in ps]}; none checked",
                      iid) for iid, ps in by_item.items() if len(ps) > 1]
    return {iid: ps[0] for iid, ps in by_item.items() if len(ps) == 1}, issues


def check_transcripts(directory: str | Path, spec: Spec, public_dir: str | Path, *,
                      items: Iterable[str] | None = None) -> TranscriptQcReport:
    """QC every `<ITEM_ID>.txt|.json` in `directory` (or only `items`) against the frozen DEV design."""
    rep = TranscriptQcReport()
    rep.public_dev = validate_public_dev(public_dir, spec)
    design: DevDesign | None = rep.public_dev.design
    if design is None:
        rep.issues.append(QcIssue("TQ001", "error", "the frozen DEV design does not validate (run `bench "
                                                    "public-check`); transcripts cannot be checked against it"))
        return rep
    cards = parse_case_cards((Path(public_dir) / DATA_FILES["cards"]).read_text(encoding="utf-8"))
    found, problems = discover_transcripts(Path(directory))
    rep.issues += problems
    wanted = set(items) if items is not None else None
    parsed: dict[str, ParsedTranscript] = {}
    for iid, path in sorted(found.items()):
        if wanted is not None and iid not in wanted:
            continue
        if iid not in design.items:
            rep.issues.append(QcIssue("TQ001", "error", f"{path.name}: not a frozen DEV design item id (name files "
                                                        "<ITEM_ID>.txt or .json)", iid))
            continue
        rep.checked[iid] = path
        try:
            parsed[iid] = parse_transcript(path)
        except (TranscriptParseError, OSError, UnicodeDecodeError) as exc:
            rep.issues.append(QcIssue("TQ002", "error", f"{path.name} does not parse (§2.3): {exc}", iid))
            continue
        rep.issues += check_item(iid, path, parsed[iid], design.items[iid], cards.get(iid), spec)
    members: dict[str, dict[str, str]] = {}
    for it in design.items.values():
        if it.pair:
            members.setdefault(it.pair[0], {})[it.pair[1]] = it.item_id
    for pid, m in sorted(members.items()):
        c, v = m.get("clean"), m.get("violating")
        if c is None or v is None:
            continue
        have = [x for x in (c, v) if x in parsed]
        if len(have) == 2:
            rep.pairs_checked.append(pid)
            rep.issues += check_pair(pid, c, parsed[c], v, parsed[v])
        elif len(have) == 1:
            rep.issues.append(QcIssue("TQ015", "warning", f"pair {pid}: only {have[0]} is in the batch; pair "
                                                          "checks (constraint 4) skipped", have[0]))
    return rep


__all__ = ["BAN_PATTERNS", "LEXICON_SEED_RULE", "QcIssue", "TranscriptQcReport", "WordBan", "card_requires_seed_check",
           "card_word_bans", "changed_turns", "check_item", "check_pair", "check_transcripts", "discover_transcripts",
           "prohibited_lexicon", "term_pattern"]
