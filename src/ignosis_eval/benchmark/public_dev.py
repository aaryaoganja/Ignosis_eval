"""Frozen Stage 5 DEV benchmark design (`bench/public/`): integrity, parsing and validation.

The repository holds only the repository-safe DEV subset of the bench-a1 design, stored verbatim:

  dev-case-cards.md          functional case cards (beats, not dialogue; transcripts are written by humans later)
  dev-master-matrix.csv      one row per DEV item
  dev-gold-blueprint.yaml    intended labels. This is intent, NOT gold: gold is labeled blind from the final
                             transcript by an independent labeler and only then reconciled against the blueprint
  gold-blueprint-schema.yaml the blueprint schema (equal to the blueprint's own `schema:` block)
  MANIFEST.json              sha256 of each file, written once at ingestion; any drift fails closed

Nothing here writes gold, registries, case-card YAML or transcripts. The validator reads the three views of each
item (matrix row, blueprint entry, case card), checks them against each other and against `rubric.yaml`,
`profile.yaml` and `frozen-contract.md` §12, and reports rule issues:

  PD001 file set / manifest / hash drift            PD010 pair integrity
  PD002 unparseable or schema-invalid file          PD011 pair members differ beyond the target (warning)
  PD003 not DEV-only (split, pack, frozen ids)      PD012 modality: header / G7 / capability / attribution
  PD004 the three views disagree                    PD013 anchor or evidence beat not in the beat sheet
  PD005 rubric vocabulary (codes, statuses, …)      PD014 evidence element not named by the rubric (warning)
  PD006 expected verdict vs V3–V5                   PD015 evidence role vs beat speaker / DC-00 (warning)
  PD007 dangerous win                               PD016 control target outside the SD-08 universe (warning)
  PD008 clean loss                                  PD017 blueprint schema block differs from the schema file
  PD009 positive outcome (AJ-09) / outcome attribution
Holdout, red-team and other private material never appears here: PD003 rejects any non-DEV row, any id outside the
DEV ids frozen in frozen-contract §12, and PD001 rejects any unexpected file.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ignosis_eval.benchmark.case_card_rules import RuleIssue
from ignosis_eval.canonical import canonical_json_bytes, sha256_bytes
from ignosis_eval.contracts.enums import (
    UNIT_MODE_INPUT,
    Attribution,
    DangerousWin,
    Disposition,
    EvaluabilityStatus,
    OutcomeAttribution,
    Pack,
    UnitMode,
    Verdict,
)
from ignosis_eval.contracts.gold_label import AttributionFacts
from ignosis_eval.contracts.registries import ControlEntry, PairEntry, Registries
from ignosis_eval.golddrv.capability import CapabilityTable
from ignosis_eval.golddrv.derive import derive_attribution
from ignosis_eval.spec.loader import Spec

DATA_FILES = {"cards": "dev-case-cards.md", "matrix": "dev-master-matrix.csv",
              "blueprint": "dev-gold-blueprint.yaml", "schema": "gold-blueprint-schema.yaml"}
MANIFEST_FILE = "MANIFEST.json"
README_FILE = "README.md"
ALLOWED_FILES = frozenset(DATA_FILES.values()) | {MANIFEST_FILE, README_FILE}
BLUEPRINT_VERSION = "bench-a1/1.0"
MANIFEST_SCHEMA = "public_dev_manifest/1.0.0"
MATRIX_COLUMNS = (
    "item_id", "pack", "split", "scenario", "category", "target_code", "secondary_code", "severity", "action_type",
    "intended_agent_behavior", "intended_customer_behavior_context", "expected_verdict", "expected_gate_status",
    "dangerous_win", "clean_loss", "primary_attribution", "modality", "language", "pair_id", "pair_role",
    "judge_bait", "abstention_type", "evidence_requirements", "reason_this_case_exists", "realism_notes",
    "benchmark_risk_notes")
GATES = tuple(f"G{i}" for i in range(1, 10))
GATE_STATUSES = ("PASS", "FAIL", "NA", "INCONCLUSIVE", "INCONCLUSIVE+trigger", "OUT_OF_SCOPE")
FIRED = ("FAIL", "INCONCLUSIVE+trigger")  # INCONCLUSIVE with an in-span trigger is reported FAIL/SUSPECTED (§6)
# The case cards list only "non-default or controlled" gates; the default reads "G1 PASS, G3 PASS, others NA,
# G7 OUT_OF_SCOPE" (checked against every "all default (...)" line).
CARD_DEFAULT_GATES = {"G1": "PASS", "G2": "NA", "G3": "PASS", "G4": "NA", "G5": "NA", "G6": "NA",
                      "G7": "OUT_OF_SCOPE", "G8": "NA", "G9": "NA"}
CARD_DEFAULT_TEXT = "G1 PASS, G3 PASS, others NA, G7 OUT_OF_SCOPE"
LABEL_CONFIDENCE = ("Sure", "Probable", "Unsure", "n/a")  # gold-blueprint-schema per_item_fields.findings
LANGUAGES = {"Hinglish": "hi-en", "English": "en", "Hindi": "hi"}
# frozen-contract §12.4: "Modes per audio item: T-gold, T-asr, A, A+T" (A+T-platform only for P-01/P-02/S-02).
AUDIO_RENDERING_MODES = (UnitMode.T_GOLD, UnitMode.T_ASR, UnitMode.A, UnitMode.A_T)
DERIVED_MARKERS = ("Derived",)  # verdict / evaluability decided after B-06 (tuning-only items)
_CHECK_ID = re.compile(r"^(G[1-9]|[A-Z]{3}-\d{2}[a-z]?)$")


class PublicDevError(RuntimeError):
    pass


# ============================================================================================ manifest
def _file_hashes(public_dir: Path) -> dict[str, str]:
    return {name: sha256_bytes((public_dir / name).read_bytes()) for name in sorted(DATA_FILES.values())}


def write_manifest(public_dir: str | Path, *, replace: bool = False) -> Path:
    """Hash-list the four data files. Written once at ingestion: an existing manifest is never overwritten unless
    `replace` is passed explicitly (a new design version, or a test fixture)."""
    public_dir = Path(public_dir)
    path = public_dir / MANIFEST_FILE
    if path.exists() and not replace:
        raise PublicDevError(f"{path} exists; the frozen DEV design manifest is write-once")
    body = {"schema_version": MANIFEST_SCHEMA, "gold_blueprint_version": BLUEPRINT_VERSION,
            "files": _file_hashes(public_dir)}
    path.write_bytes(canonical_json_bytes(body) + b"\n")
    return path


def manifest_sha256(public_dir: str | Path) -> str:
    return sha256_bytes((Path(public_dir) / MANIFEST_FILE).read_bytes())


def _check_files(public_dir: Path) -> list[RuleIssue]:
    out: list[RuleIssue] = []
    if not public_dir.is_dir():
        return [RuleIssue("PD001", "error", f"{public_dir} does not exist")]
    present = {p.name for p in public_dir.iterdir() if not p.name.startswith(".")}
    for name in sorted(present - ALLOWED_FILES):
        out.append(RuleIssue("PD001", "error", f"unexpected file {name} in bench/public (DEV design only; private "
                                               "benchmark material never lives in the repository)"))
    for name in sorted((set(DATA_FILES.values()) | {MANIFEST_FILE}) - present):
        out.append(RuleIssue("PD001", "error", f"missing {name}"))
    if out:
        return out
    try:
        man = json.loads((public_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [RuleIssue("PD001", "error", f"{MANIFEST_FILE} invalid: {exc}")]
    if man.get("schema_version") != MANIFEST_SCHEMA or man.get("gold_blueprint_version") != BLUEPRINT_VERSION:
        out.append(RuleIssue("PD001", "error", f"{MANIFEST_FILE} version mismatch"))
    want, have = man.get("files", {}), _file_hashes(public_dir)
    if set(want) != set(have):
        out.append(RuleIssue("PD001", "error", f"{MANIFEST_FILE} lists {sorted(want)}, expected {sorted(have)}"))
    for name in sorted(set(want) & set(have)):
        if want[name] != have[name]:
            out.append(RuleIssue("PD001", "error", f"hash drift in {name} (frozen design modified; fail closed)"))
    return out


# ============================================================================================ blueprint
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlueprintGate(_Strict):
    status: Literal["PASS", "FAIL", "NA", "INCONCLUSIVE", "INCONCLUSIVE+trigger", "OUT_OF_SCOPE"]
    label_type: str


class BlueprintFinding(_Strict):
    code: str
    severity: str
    action: str
    anchor_beat: str
    evidence_elements: str
    attribution_T: str
    label_type: str
    note: str


class BlueprintOutcome(_Strict):
    dispositions: str
    positive: bool
    outcome_attribution: str


class BlueprintItem(_Strict):
    item_id: str
    pack: str
    split: str
    modes: str
    header: str | None
    gates: dict[str, BlueprintGate] | Literal["n/a"]
    findings: list[BlueprintFinding]
    verdict: str
    evaluability: str
    outcome: BlueprintOutcome
    dangerous_win: str
    clean_loss: bool
    attribution_summary: str
    control_target_gates: list[str]
    abstention_targets: list[Any]
    contested: bool
    label_confidence: str
    ambiguity_notes: str
    depends_on: list[Any]


class BlueprintSchema(_Strict):
    gold_blueprint_version: str
    principle: str
    anchors: str
    per_item_fields: dict[str, str]
    label_type_legend: dict[str, str]
    mode_derivation: str


class Blueprint(_Strict):
    schema_block: BlueprintSchema = Field(alias="schema")
    items: list[BlueprintItem]


class SchemaFile(_Strict):
    schema_block: BlueprintSchema = Field(alias="schema")


# ============================================================================================ case cards
@dataclass(frozen=True)
class Beat:
    n: int
    speaker: str | None  # A agent, C borrower (customer), T third party; None when the beat sheet does not say
    text: str


@dataclass(frozen=True)
class CardFinding:
    code: str
    severity: str
    action: str
    anchor_beat: str
    elements: str
    attribution_T: str
    label_type: str
    note: str  # "-" when absent


@dataclass
class DesignCard:
    item_id: str
    category: str
    fields: dict[str, str] = field(default_factory=dict)  # raw labelled paragraphs
    identity: dict[str, str] = field(default_factory=dict)
    findings: list[CardFinding] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)  # verdict line, confidence line, must-not-fire
    beats: dict[int, Beat] = field(default_factory=dict)
    beat_refs: list[tuple[int | None, int | None, str]] = field(default_factory=list)  # (from, to, ref item)
    free_beats: list[str] = field(default_factory=list)


_SECTION = re.compile(r"^### (?P<id>\S+) — (?P<category>.+)$")
_FIELD = re.compile(r"^\*\*(?P<label>[^*]+?)\.\*\* ?(?P<value>.*)$")
_CARD_FINDING = re.compile(
    r"^- `(?P<code>[^`]+)` (?P<severity>[A-Z]+) / (?P<action>[A-Z+]+) — anchor (?P<anchor>B\d+); "
    r"elements: (?P<elements>.*?); attribution \(T\): (?P<attr>[A-Z_]+); label type: (?P<lt>.*?)"
    r"(?:; note: (?P<note>.*))?$")
_VERDICT = re.compile(
    r"^- Verdict \*\*(?P<verdict>[^*]+)\*\* · evaluability (?P<evaluability>\S+) · outcome (?P<outcome>.*) "
    r"\(positive=(?P<positive>True|False), attribution (?P<oattr>[A-Z_-]+)\) · Dangerous Win (?P<dw>\S+) · "
    r"Clean Loss (?P<cl>True|False)$")
_REPAIR = re.compile(r"^- Repairability: (?P<repair>.*?) · label confidence (?P<lc>\S+)(?: · ambiguity: (?P<amb>.*))?$")
_BEAT_REF = re.compile(r"^- B(?P<a>\d+)-B(?P<b>\d+) (?:as|identical to) (?P<ref>[A-Z][A-Z0-9-]*[A-Z0-9])$")
_BEAT_RANGE = re.compile(r"^- B(?P<a>\d+)-B(?P<b>\d+) (?P<text>.+)$")
_BEAT_SPK = re.compile(r"^- B(?P<n>\d+) (?P<spk>[ACT]) (?P<text>.+)$")
_BEAT = re.compile(r"^- B(?P<n>\d+) (?P<text>.+)$")
_BEAT_ALL = re.compile(r"^- as (?P<ref>[A-Z][A-Z0-9-]*[A-Z0-9])$")


def parse_case_cards(text: str) -> dict[str, DesignCard]:
    cards: dict[str, DesignCard] = {}
    card: DesignCard | None = None
    mode = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        m = _SECTION.match(line)
        if m:
            if m["id"] in cards:
                raise PublicDevError(f"duplicate case card {m['id']}")
            card = cards[m["id"]] = DesignCard(m["id"], m["category"])
            mode = ""
            continue
        if card is None or not line or line == "---" or line.startswith("## "):
            if line.startswith("## "):
                card = None
            continue
        fm = _FIELD.match(line)
        if fm:
            label = fm["label"]
            card.fields[label] = fm["value"]
            mode = "labels" if label == "Expected labels" else "beats" if label.startswith("Evidence specification") \
                else ""
            continue
        if not line.startswith("- "):
            raise PublicDevError(f"{card.item_id}: unrecognised line {line!r}")
        if mode == "labels":
            if (f := _CARD_FINDING.match(line)) is not None:
                card.findings.append(CardFinding(f["code"], f["severity"], f["action"], f["anchor"], f["elements"],
                                                 f["attr"], f["lt"], f["note"] or "-"))
            elif (v := _VERDICT.match(line)) is not None:
                card.labels.update({k: v[k] for k in ("verdict", "evaluability", "outcome", "positive", "oattr",
                                                       "dw", "cl")})
            elif (r := _REPAIR.match(line)) is not None:
                card.labels.update({"repair": r["repair"], "label_confidence": r["lc"], "ambiguity": r["amb"] or "-"})
            elif line.startswith("- Must-not-fire targets: "):
                card.labels["must_not_fire"] = line.removeprefix("- Must-not-fire targets: ")
            elif line == "- No findings.":
                card.labels["no_findings"] = "true"
            else:
                raise PublicDevError(f"{card.item_id}: unrecognised label line {line!r}")
        elif mode == "beats":
            if (b := _BEAT_REF.match(line)) is not None:
                card.beat_refs.append((int(b["a"]), int(b["b"]), b["ref"]))
            elif (b := _BEAT_ALL.match(line)) is not None:
                card.beat_refs.append((None, None, b["ref"]))
            elif (b := _BEAT_RANGE.match(line)) is not None:
                for n in range(int(b["a"]), int(b["b"]) + 1):
                    card.beats[n] = Beat(n, None, b["text"])
            elif (b := _BEAT_SPK.match(line)) is not None:
                card.beats[int(b["n"])] = Beat(int(b["n"]), b["spk"], b["text"])
            elif (b := _BEAT.match(line)) is not None:
                card.beats[int(b["n"])] = Beat(int(b["n"]), None, b["text"])
            else:
                card.free_beats.append(line[2:])
        else:
            raise PublicDevError(f"{card.item_id}: list line outside labels / beats: {line!r}")
    for c in cards.values():
        ident = c.fields.get("Identity", "")
        for part in ident.split(" · "):
            key, _, val = part.partition(" ")
            c.identity[key.lower()] = val.strip("`")
    return cards


def resolve_beats(cards: dict[str, DesignCard]) -> dict[str, dict[int, Beat]]:
    """Beats of every card with inherited ranges ("B1-B3 as G-02", "as G-02") copied from the referenced card."""
    out: dict[str, dict[int, Beat]] = {}

    def resolve(iid: str, stack: tuple[str, ...]) -> dict[int, Beat]:
        if iid in out:
            return out[iid]
        if iid in stack:
            raise PublicDevError(f"circular beat reference via {iid}")
        c = cards[iid]
        beats: dict[int, Beat] = {}
        for a, b, ref in c.beat_refs:
            if ref not in cards:
                raise PublicDevError(f"{iid}: beats refer to unknown card {ref}")
            src = resolve(ref, stack + (iid,))
            wanted = range(a, b + 1) if a is not None and b is not None else sorted(src)
            for n in wanted:
                if n not in src:
                    raise PublicDevError(f"{iid}: inherited beat B{n} does not exist in {ref}")
                beats[n] = src[n]
        beats.update(c.beats)
        out[iid] = beats
        return beats

    for iid in cards:
        resolve(iid, ())
    return out


# ============================================================================================ small parsers
def parse_elements(text: str) -> dict[str, list[int]]:
    """'hedge_turn=B6,B8; firm_treatment_turn=B7,B9' -> {'hedge_turn': [6, 8], ...}; '(loan_existence)' dropped."""
    out: dict[str, list[int]] = {}
    for part in (p.strip() for p in text.split(";")):
        if not part:
            continue
        m = re.match(r"^(?P<name>[a-z_]+)=(?P<beats>B\d+(?:,B\d+)*)(?:\s*\([^)]*\))?$", part)
        if m is None:
            raise PublicDevError(f"unparseable evidence element {part!r}")
        out[m["name"]] = [int(b[1:]) for b in m["beats"].split(",")]
    return out


def parse_dispositions(text: str) -> list[tuple[str, list[str]]]:
    """'SETTLEMENT_REQUESTED + OFFER_AGREED + PTP_STATED(firm)' -> [(name, [qualifiers]), ...]."""
    out = []
    for part in (p.strip() for p in text.split(" + ")):
        m = re.match(r"^(?P<name>[A-Z_]+)(?:\((?P<q>[^)]*)\))?$", part)
        if m is None:
            raise PublicDevError(f"unparseable disposition {part!r}")
        out.append((m["name"], [q.strip() for q in (m["q"] or "").split(",") if q.strip()]))
    return out


def parse_targets(text: str) -> list[tuple[str, bool, str]]:
    """Matrix target/secondary column -> [(check id, negative, qualifier)]; non-check targets (normalizer, tuning)
    and '-' yield []."""
    out = []
    for part in re.split(r",\s*(?![^()]*\))", text.strip()):
        m = re.match(r"^(?P<code>[A-Za-z0-9-]+)(?:\s*\((?P<q>[^)]*)\))?$", part.strip())
        if m is None or not _CHECK_ID.match(m["code"]):
            continue
        q = m["q"] or ""
        out.append((m["code"], "negative" in q, q))
    return out


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def parse_header_time(header: str | None) -> time | None:
    m = re.search(r"\(e\.g\. (\d{1,2}):(\d{2}) IST\)", header or "")
    return time(int(m[1]), int(m[2])) if m else None


def parse_matrix_gates(text: str) -> dict[str, str]:
    out = {}
    for part in (p.strip() for p in text.split(";")):
        g, _, st = part.partition("=")
        out[g.strip()] = st.strip()
    return out


def parse_card_gates(text: str) -> dict[str, str]:
    """'Gates (non-default or controlled): G2=PASS, G3=FAIL' or '... all default (G1 PASS, ...)'."""
    body = text.split(":", 1)[1].strip() if ":" in text else text
    gates = dict(CARD_DEFAULT_GATES)
    if body.startswith("all default"):
        if f"({CARD_DEFAULT_TEXT})" not in body:
            raise PublicDevError(f"card default gates changed: {body!r}")
        return gates
    for part in (p.strip() for p in body.split(",")):
        g, _, st = part.partition("=")
        if g not in gates:
            raise PublicDevError(f"unknown gate in card: {part!r}")
        gates[g] = st
    return gates


def unit_modes_of(modes: str, design_ids: set[str]) -> tuple[list[UnitMode], list[str]]:
    """'T; A (G-02@A dev rendering); G-02-N5 (...)' -> ([TRANSCRIPT, T-gold, T-asr, A, A+T], ['G-02-N5']).

    'T' is the TRANSCRIPT unit. 'A (<id>@A ... rendering)' is the item's audio rendering, whose units are the §12.4
    audio modes. Bare 'A' / 'A+T' are unit modes of an audio item. A token naming another design item is a
    cross-reference (a variant), not a mode."""
    out: list[UnitMode] = []
    refs: list[str] = []
    for tok in (t.strip() for t in re.split(r"[;,]\s*(?![^()]*\))", modes)):
        base = re.sub(r"\s*\(.*\)$", "", tok).strip()
        if base in design_ids:
            refs.append(base)
        elif base == "T":
            out.append(UnitMode.TRANSCRIPT)
        elif base == "A" and "rendering" in tok:
            out += list(AUDIO_RENDERING_MODES)
        else:
            try:
                out.append(UnitMode(base))
            except ValueError as exc:
                raise PublicDevError(f"unknown modality token {tok!r}") from exc
    if len(set(out)) != len(out):
        raise PublicDevError(f"duplicate unit modes in {modes!r}")
    return out, refs


def design_pack(pack: str, category: str) -> Pack:
    """Design pack vocabulary (frozen-contract §12 headings) -> ItemMeta pack. The contract's language pack holds
    twins and snippets; ItemMeta keeps them apart (convention, docs/spec-reconciliation.md §3)."""
    if pack == "language":
        return Pack.SNIPPET if category.startswith("Snippet") else Pack.LANGUAGE_TWIN
    return Pack(pack)


# ============================================================================================ frozen-contract §12
@dataclass(frozen=True)
class FrozenDev:
    ids: frozenset[str]
    pairs: dict[str, tuple[str, str]]  # item -> (pair id, role)
    audio_renderings: frozenset[str]


def frozen_dev(contract_md: str) -> FrozenDev:
    """The DEV entries of frozen-contract §12 (only lines marked dev are read)."""
    start, end = contract_md.find("## 12."), contract_md.find("## 13.")
    if start < 0 or end < 0:
        raise PublicDevError("frozen-contract.md has no §12 benchmark section")
    ids: set[str] = set()
    pairs: dict[str, tuple[str, str]] = {}
    renders: set[str] = set()
    for line in contract_md[start:end].splitlines():
        row = re.match(r"^\| (?P<id>[A-Z][A-Z0-9-]*) \| [^|]* \| (?P<split>\w+) \| (?P<pair>[^|]*) \|$", line)
        if row and row["split"] == "dev":
            ids.add(row["id"])
            pm = re.match(r"^(?P<p>[A-Z]P-\d{2}) (?P<r>clean|violating)$", row["pair"].strip())
            if pm:
                pairs[row["id"]] = (pm["p"], pm["r"])
        if line.startswith("- **Dev (8):**"):
            ids |= set(re.findall(r"\bMD-[A-Z]\d\b", line))
        if line.startswith("- **Dev:**"):
            renders |= set(re.findall(r"\b([A-Z]-\d{2})@audio\b", line))
            ids |= set(re.findall(r"\b[A-Z]-\d{2}-N\d+\b", line))
        sn = re.search(r"\bSN-D(\d{2})–D(\d{2}) \(dev\)", line)
        if sn:
            ids |= {f"SN-D{n:02d}" for n in range(int(sn[1]), int(sn[2]) + 1)}
    return FrozenDev(frozenset(ids), pairs, frozenset(renders))


# ============================================================================================ merged design
@dataclass
class DesignItem:
    item_id: str
    pack: Pack
    split: str
    language: str  # ItemMeta language code
    unit_modes: list[UnitMode]
    has_header: bool
    header_time: time | None
    gates: dict[str, str] | None  # None for snippets (component tests)
    findings: list[BlueprintFinding]
    verdict: str
    evaluability: str
    positive: bool
    dispositions: list[tuple[str, list[str]]]
    outcome_attribution: str
    dangerous_win: str
    clean_loss: bool
    control_target_gates: list[str]
    pair: tuple[str, str] | None
    targets: list[tuple[str, bool, str]]
    secondary: list[tuple[str, bool, str]]
    beats: dict[int, Beat]
    tuning_only: bool  # verdict derived after B-06; never used for scoring claims

    @property
    def scored(self) -> bool:
        return self.gates is not None and not self.tuning_only


@dataclass
class DevDesign:
    items: dict[str, DesignItem]
    manifest_sha256: str

    def registries(self) -> Registries:
        """Pairs and controls implied by the frozen design (not written anywhere; bench check compares a populated
        bench/registries.json against this)."""
        by_pair: dict[str, dict[str, DesignItem]] = {}
        for it in self.items.values():
            if it.pair:
                by_pair.setdefault(it.pair[0], {})[it.pair[1]] = it
        pairs = []
        for pid, members in sorted(by_pair.items()):
            v = members["violating"]
            pairs.append(PairEntry(pair_id=pid, clean_item=members["clean"].item_id, violating_item=v.item_id,
                                   target_check=v.targets[0][0]))
        controls = [ControlEntry(item_id=it.item_id, target_gates=it.control_target_gates)  # type: ignore[arg-type]
                    for it in sorted(self.items.values(), key=lambda x: x.item_id) if it.control_target_gates]
        return Registries(controls=controls, pairs=pairs)


@dataclass
class PublicDevReport:
    issues: list[RuleIssue] = field(default_factory=list)
    design: DevDesign | None = None

    @property
    def errors(self) -> list[RuleIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[RuleIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


# ============================================================================================ validation
class _V:
    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.issues: list[RuleIssue] = []
        self.raw = {c["id"]: c for c in spec.rubric["gates"] + spec.rubric["codes"] + spec.rubric["platform_signals"]}
        self.table = CapabilityTable.from_rubric(spec.rubric)

    def err(self, rule: str, msg: str, iid: str | None = None) -> None:
        self.issues.append(RuleIssue(rule, "error", msg, iid))

    def warn(self, rule: str, msg: str, iid: str | None = None) -> None:
        self.issues.append(RuleIssue(rule, "warning", msg, iid))

    def eq(self, iid: str, what: str, **views: object) -> None:
        vals = list(views.values())
        if any(v != vals[0] for v in vals[1:]):
            self.err("PD004", f"{what} disagrees: " + "; ".join(f"{k}={v!r}" for k, v in views.items()), iid)

    # ---------------------------------------------------------------- rubric lookups
    def elements(self, code: str) -> list[dict]:
        c = self.raw[code]
        els = list(c.get("required_evidence_elements") or [])
        if isinstance(c.get("sub_rules"), dict):
            for sub in c["sub_rules"].values():
                if isinstance(sub, dict):
                    els += sub.get("required_evidence_elements") or []
        return els

    def severity(self, code: str) -> str | None:
        c = self.raw[code]
        if c.get("severity"):
            return str(c["severity"])
        m = re.search(r"default: (CRITICAL|MAJOR|MINOR|INFORMATIONAL)", c.get("severity_rule", ""))
        return m[1] if m else None

    def actions(self, code: str) -> tuple[set[str], bool]:
        """(allowed action tokens, exact). A fixed action_type must match exactly; an action_type_rule allows one of
        the actions it names."""
        c = self.raw[code]
        if c.get("action_type"):
            at = c["action_type"]
            return set(at if isinstance(at, list) else [at]), True
        return set(re.findall(r"\b(REVIEW|REMEDIATE|FIX)\b", c.get("action_type_rule", ""))), False


def _registered(note: str) -> bool:
    """Registration-override fact from the blueprint note ('override: B5 acknowledges' vs 'no registration')."""
    low = note.lower()
    return "override" in low and "no registration" not in low


def validate_public_dev(public_dir: str | Path, spec: Spec, *, contract_md: str | None = None) -> PublicDevReport:
    public_dir = Path(public_dir)
    rep = PublicDevReport()
    rep.issues += _check_files(public_dir)
    if rep.errors:
        return rep
    v = _V(spec)
    contract_md = contract_md if contract_md is not None else \
        (spec.spec_dir / "frozen-contract.md").read_text(encoding="utf-8")
    try:
        text = {k: (public_dir / n).read_bytes().decode("utf-8") for k, n in DATA_FILES.items()}
        blueprint = Blueprint.model_validate(yaml.safe_load(text["blueprint"]))
        schema_file = SchemaFile.model_validate(yaml.safe_load(text["schema"]))
        reader = csv.DictReader(io.StringIO(text["matrix"], newline=""))
        if tuple(reader.fieldnames or ()) != MATRIX_COLUMNS:
            raise PublicDevError(f"matrix columns {reader.fieldnames} != {list(MATRIX_COLUMNS)}")
        rows = list(reader)
        cards = parse_case_cards(text["cards"])
        beats = resolve_beats(cards)
        frozen = frozen_dev(contract_md)
    except (PublicDevError, ValidationError, yaml.YAMLError, UnicodeDecodeError, csv.Error) as exc:
        rep.issues.append(RuleIssue("PD002", "error", f"{type(exc).__name__}: {exc}"))
        return rep
    # ---------------------------------------------------------------- schema file
    if blueprint.schema_block != schema_file.schema_block:
        v.err("PD017", "the blueprint's schema block differs from gold-blueprint-schema.yaml")
    if schema_file.schema_block.gold_blueprint_version != BLUEPRINT_VERSION:
        v.err("PD017", f"gold_blueprint_version {schema_file.schema_block.gold_blueprint_version} != "
                       f"{BLUEPRINT_VERSION}")
    # ---------------------------------------------------------------- item sets and DEV-only
    ids_m = [r["item_id"] for r in rows]
    ids_b = [i.item_id for i in blueprint.items]
    for name, ids in (("matrix", ids_m), ("blueprint", ids_b)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            v.err("PD004", f"duplicate item ids in the {name}: {dup}")
    if set(ids_m) != set(ids_b) or set(ids_m) != set(cards):
        v.err("PD004", f"item sets differ: matrix-only {sorted(set(ids_m) - set(ids_b))}, blueprint-only "
                       f"{sorted(set(ids_b) - set(ids_m))}, card-only {sorted(set(cards) - set(ids_m))}, "
                       f"missing cards {sorted(set(ids_m) - set(cards))}")
    matrix = {r["item_id"]: r for r in rows}
    bp = {i.item_id: i for i in blueprint.items}
    for iid in sorted(set(ids_m) | set(ids_b)):
        if iid not in frozen.ids:
            v.err("PD003", "not a DEV item of frozen-contract §12 (only the DEV subset may live in the repository)",
                  iid)
    for iid in sorted(frozen.ids - set(ids_m)):
        v.err("PD004", "DEV item frozen in §12 is missing from the design files", iid)
    common = sorted(set(matrix) & set(bp) & set(cards))
    design_ids = set(common)
    items: dict[str, DesignItem] = {}
    for iid in common:
        row, b, c = matrix[iid], bp[iid], cards[iid]
        try:
            items[iid] = _item(v, iid, row, b, c, beats[iid], design_ids)
        except PublicDevError as exc:
            v.err("PD002", str(exc), iid)
    # G-02-N5 style outcome references ("As G-02") resolve against the referenced item
    for it in items.values():
        _check_item(v, it, bp[it.item_id], items, frozen)
    _check_pairs(v, items, frozen, matrix)
    rep.issues += v.issues
    if not any(i.severity == "error" and i.rule_id in ("PD002", "PD004") for i in rep.issues):
        rep.design = DevDesign(items, manifest_sha256(public_dir))
    return rep


def _item(v: _V, iid: str, row: dict[str, str], b: BlueprintItem, c: DesignCard, beats: dict[int, Beat],
          design_ids: set[str]) -> DesignItem:
    ident = c.identity
    # ---------------------------------------------------------------- identity, split, pack, modality, language
    v.eq(iid, "pack", matrix=row["pack"], blueprint=b.pack, card=ident.get("pack"))
    v.eq(iid, "split", matrix=row["split"], blueprint=b.split, card=ident.get("split"))
    v.eq(iid, "modality", matrix=row["modality"], blueprint=b.modes, card=ident.get("modality"))
    v.eq(iid, "language", matrix=row["language"], card=ident.get("language"))
    v.eq(iid, "header", blueprint=b.header, card=ident.get("header"))
    v.eq(iid, "category", matrix=row["category"], card=c.category)
    if row["split"] != "dev" or b.split != "dev":
        v.err("PD003", f"split {row['split']!r}: only the DEV split may live in bench/public", iid)
    if row["pack"] in ("redteam", "calibration", "abstention"):
        v.err("PD003", f"pack {row['pack']!r} has no DEV items (§12.3, §12.6, §12.7)", iid)
    if row["language"] not in LANGUAGES:
        v.err("PD005", f"language {row['language']!r} not in {sorted(LANGUAGES)}", iid)
    elif LANGUAGES[row["language"]] not in v.spec.profile["languages_supported"]:
        v.err("PD005", f"language {row['language']} is not supported by the profile", iid)
    pack = design_pack(row["pack"], row["category"])
    modes, _refs = unit_modes_of(row["modality"], design_ids)  # refs name other design items (variants)
    # ---------------------------------------------------------------- text fields shared by matrix and card
    # the card sentence-terminates the scenario before "*Why it exists:*"; the matrix cell does not
    scen = re.match(r"^(?P<s>.*?)\.? \*Why it exists:\* (?P<w>.*)$", c.fields.get("Scenario / purpose", ""))
    hidden = re.match(r"^Target (?P<t>.*?)\. Judge-bait: (?P<j>.*)$", c.fields.get("Hidden test intent", ""))
    notes = re.match(r"^(?P<n>.*?)\. Realism: (?P<r>.*?)\. Risk: (?P<k>.*)$", c.fields.get("Authoring notes", ""))
    if not (scen and hidden and notes):
        raise PublicDevError("case card lacks Scenario / Hidden test intent / Authoring notes in the frozen form")
    target_text, _, secondary_text = hidden["t"].partition("; secondary ")
    for what, m_val, c_val in (
            ("scenario", row["scenario"], scen["s"]), ("reason_this_case_exists", row["reason_this_case_exists"],
                                                       scen["w"]),
            ("intended_customer_behavior_context", row["intended_customer_behavior_context"],
             c.fields.get("Customer context")),
            ("intended_agent_behavior", row["intended_agent_behavior"],
             c.fields.get("Intended defect / agent behavior in this case")),
            ("judge_bait", row["judge_bait"], hidden["j"]), ("target_code", row["target_code"], target_text),
            ("secondary_code", row["secondary_code"], secondary_text or "-"),
            ("realism_notes", row["realism_notes"], notes["r"]), ("benchmark_risk_notes", row["benchmark_risk_notes"],
                                                                   notes["k"])):
        v.eq(iid, what, matrix=m_val, card=c_val)
    ev_label = next((k for k in c.fields if k.startswith("Evidence specification")), None)
    ev_text = c.fields.get(ev_label, "") if ev_label else ""
    if row["evidence_requirements"] != "-" or ev_text != "reference_date + expected normalized value":
        v.eq(iid, "evidence_requirements", matrix=row["evidence_requirements"], card=ev_text)
    if row["abstention_type"] != "-":
        v.err("PD003", f"abstention_type {row['abstention_type']!r}: the abstention pack is holdout-only (§12.3)", iid)
    # ---------------------------------------------------------------- pair
    pair_card = c.fields.get("Counterfactual / pair", "none")
    pm = re.match(r"^(?P<p>[A-Z]P-\d{2}) \((?P<r>clean|violating)\)$", pair_card)
    card_pair = (pm["p"], pm["r"]) if pm else None
    matrix_pair = (row["pair_id"], row["pair_role"]) if row["pair_id"] != "-" else None
    if pair_card != "none" and pm is None:
        v.err("PD002", f"unparseable pair line {pair_card!r}", iid)
    v.eq(iid, "pair", matrix=matrix_pair, card=card_pair)
    # ---------------------------------------------------------------- gates
    gates: dict[str, str] | None = None
    if b.gates == "n/a":
        if row["expected_gate_status"] != "-":
            v.err("PD004", "blueprint gates n/a but the matrix lists gate statuses", iid)
    else:
        gates = {g: gb.status for g, gb in b.gates.items()}
        if tuple(gates) != GATES:
            v.err("PD005", f"blueprint gates must be exactly {list(GATES)} in order, got {list(gates)}", iid)
        mg = parse_matrix_gates(row["expected_gate_status"])
        v.eq(iid, "gate statuses (G1-G7)", matrix=mg, blueprint={g: gates.get(g) for g in mg})
        if list(mg) != list(GATES[:7]):
            v.err("PD004", f"matrix expected_gate_status must list G1-G7, got {list(mg)}", iid)
        cg = parse_card_gates(c.fields.get("Expected labels", ""))
        v.eq(iid, "gate statuses", card=cg, blueprint=gates)
    # ---------------------------------------------------------------- findings
    cf = [(f.code, f.severity, f.action, f.anchor_beat, f.elements, f.attribution_T, f.label_type, f.note)
          for f in c.findings]
    bf = [(f.code, f.severity, f.action, f.anchor_beat, f.evidence_elements, f.attribution_T, f.label_type, f.note)
          for f in b.findings]
    v.eq(iid, "findings", card=cf, blueprint=bf)
    if not b.findings and gates is not None and "no_findings" not in c.labels:
        v.err("PD004", "no blueprint findings but the card does not say 'No findings.'", iid)
    # ---------------------------------------------------------------- verdict, outcome, tags, meta
    lab = c.labels
    if gates is not None:
        v.eq(iid, "verdict", matrix=row["expected_verdict"], blueprint=b.verdict, card=lab.get("verdict"))
        v.eq(iid, "evaluability", blueprint=b.evaluability, card=lab.get("evaluability"))
        v.eq(iid, "outcome", blueprint=b.outcome.dispositions, card=lab.get("outcome"))
        v.eq(iid, "positive", blueprint=b.outcome.positive, card=lab.get("positive") == "True")
        v.eq(iid, "outcome_attribution", blueprint=b.outcome.outcome_attribution, card=lab.get("oattr"))
        v.eq(iid, "dangerous_win", matrix=row["dangerous_win"], blueprint=b.dangerous_win, card=lab.get("dw"))
        v.eq(iid, "clean_loss", matrix=row["clean_loss"], blueprint=str(b.clean_loss), card=lab.get("cl"))
        v.eq(iid, "label_confidence", blueprint=b.label_confidence, card=lab.get("label_confidence"))
        v.eq(iid, "ambiguity", blueprint=b.ambiguity_notes, card=lab.get("ambiguity"))
        mnf = [g.strip() for g in lab.get("must_not_fire", "").split(",") if g.strip()]
        v.eq(iid, "control target gates", blueprint=b.control_target_gates, card=mnf)
    else:
        v.eq(iid, "verdict", matrix=row["expected_verdict"], blueprint=b.verdict)
        v.eq(iid, "clean_loss", matrix=row["clean_loss"], blueprint=str(b.clean_loss))
    v.eq(iid, "attribution summary", matrix=row["primary_attribution"], blueprint=b.attribution_summary)
    targets = parse_targets(row["target_code"])
    secondary = parse_targets(row["secondary_code"])
    tuning_only = b.verdict.startswith(DERIVED_MARKERS) or b.evaluability in DERIVED_MARKERS
    if b.outcome.dispositions.startswith("As "):
        dispositions: list[tuple[str, list[str]]] = [("__REF__", [b.outcome.dispositions[3:]])]
    elif b.outcome.dispositions == "n/a":
        dispositions = []
    else:
        try:
            dispositions = parse_dispositions(b.outcome.dispositions)
        except PublicDevError as exc:
            v.err("PD005", str(exc), iid)
            dispositions = []
    return DesignItem(
        item_id=iid, pack=pack, split=row["split"], language=LANGUAGES.get(row["language"], "other"),
        unit_modes=modes, has_header=b.header is not None, header_time=parse_header_time(b.header), gates=gates,
        findings=b.findings, verdict=b.verdict, evaluability=b.evaluability, positive=b.outcome.positive,
        dispositions=dispositions, outcome_attribution=b.outcome.outcome_attribution, dangerous_win=b.dangerous_win,
        clean_loss=b.clean_loss, control_target_gates=b.control_target_gates, pair=matrix_pair, targets=targets,
        secondary=secondary, beats=beats, tuning_only=tuning_only)


def _check_item(v: _V, it: DesignItem, b: BlueprintItem, items: dict[str, DesignItem], frozen: FrozenDev) -> None:
    iid = it.item_id
    reg = v.spec.registry
    # ---------------------------------------------------------------- modality and audio renderings (§12.4)
    has_rendering = UnitMode.A in it.unit_modes and UnitMode.TRANSCRIPT in it.unit_modes
    if has_rendering != (iid in frozen.audio_renderings):
        v.err("PD012", f"audio rendering {'declared' if has_rendering else 'missing'} but §12.4 "
                       f"{'does not list' if has_rendering else 'lists'} {iid}@audio", iid)
    if b.label_confidence not in LABEL_CONFIDENCE:
        v.err("PD005", f"label_confidence {b.label_confidence!r} not in {list(LABEL_CONFIDENCE)}", iid)
    if it.gates is None:  # snippets: component tests (SD-22), no gates / verdict
        if it.pack is not Pack.SNIPPET:
            v.err("PD005", "only snippet items may omit gates", iid)
        return
    # ---------------------------------------------------------------- vocabulary
    for g, st in it.gates.items():
        if st not in GATE_STATUSES:
            v.err("PD005", f"{g} status {st!r} not in {list(GATE_STATUSES)}", iid)
    for g in ("G8", "G9"):
        if it.gates.get(g) != "NA":
            v.err("PD005", f"{g} must be NA: the default profile configures no {g} lists", iid)
    if it.gates.get("G3") == "NA":
        v.err("PD005", "G3 is never NA in an evaluable call (rubric G3 na_when)", iid)
    valid_disp = {d.value for d in Disposition}
    firmness_vocab = set(v.spec.rubric["extraction_vocabulary"]["firmness"])
    disps = it.dispositions
    if disps and disps[0][0] == "__REF__":
        ref = items.get(disps[0][1][0])
        disps = ref.dispositions if ref else []
        if ref is None:
            v.err("PD004", f"outcome refers to unknown item {it.dispositions[0][1][0]}", iid)
    firm: str | None = None
    for name, quals in disps:
        if name not in valid_disp:
            v.err("PD005", f"disposition {name} not in rubric outcome_model.observable_dispositions", iid)
        if name == "PTP_STATED":
            fq = [q for q in quals if q in firmness_vocab]
            if len(fq) != 1:
                v.err("PD005", f"PTP_STATED needs exactly one firmness qualifier from {sorted(firmness_vocab)}", iid)
            firm = fq[0] if fq else None
    if it.outcome_attribution not in {o.value for o in OutcomeAttribution}:
        v.err("PD005", f"outcome_attribution {it.outcome_attribution!r} unknown", iid)
    if it.dangerous_win not in {d.value for d in DangerousWin}:
        v.err("PD005", f"dangerous_win {it.dangerous_win!r} unknown", iid)
    for g in it.control_target_gates:
        if g not in GATES:
            v.err("PD005", f"control target {g} is not a gate", iid)
    # ---------------------------------------------------------------- findings: codes, severity, action, beats
    gate_findings = {f.code for f in it.findings if f.code in GATES}
    for f in it.findings:
        if f.code not in v.raw or f.code in reg.not_evaluated or f.code in reg.oos_codes:
            v.err("PD005", f"finding code {f.code} is not an MVP-scored rubric check", iid)
            continue
        if f.code in GATES and it.gates.get(f.code) not in FIRED:
            v.err("PD005", f"gate finding {f.code} needs gate status FAIL or INCONCLUSIVE+trigger", iid)
        if f.severity != v.severity(f.code):
            v.err("PD005", f"{f.code} severity {f.severity} != rubric {v.severity(f.code)}", iid)
        allowed, exact = v.actions(f.code)
        acts = set(f.action.split("+"))
        if (exact and acts != allowed) or (not exact and not (len(acts) == 1 and acts <= allowed)):
            v.err("PD005", f"{f.code} action {f.action} disagrees with rubric {sorted(allowed)}", iid)
        if f.attribution_T not in {a.value for a in Attribution}:
            v.err("PD005", f"{f.code} attribution {f.attribution_T!r} unknown", iid)
        # anchors, elements, roles
        try:
            els = parse_elements(f.evidence_elements)
        except PublicDevError as exc:
            v.err("PD002", f"{f.code}: {exc}", iid)
            els = {}
        anchor = int(f.anchor_beat[1:])
        for n in [anchor] + [n for ns in els.values() for n in ns]:
            if n not in it.beats:
                v.err("PD013", f"{f.code}: beat B{n} is not in the beat sheet", iid)
        rubric_els = {e["name"]: e for e in v.elements(f.code)}
        for name, ns in els.items():
            spec_el = rubric_els.get(name)
            if spec_el is None:
                v.warn("PD014", f"{f.code}: evidence element {name!r} is not a rubric required_evidence_element "
                                f"({sorted(rubric_els)}); SD-14 completeness needs rubric names — reconcile before "
                                "labeling", iid)
                continue
            want = {"agent": "A", "borrower": "C"}.get(spec_el.get("role", ""))
            for n in ns:
                spk = it.beats[n].speaker if n in it.beats else None
                if want is None or spk is None or spk == want:
                    continue
                if want == "C" and spk == "T":
                    v.warn("PD015", f"{f.code}: {name}=B{n} is spoken by the third party; the rubric role is "
                                    "borrower, so the transcript must label the customer-side speaker BORROWER "
                                    "(OTHER would leave the call without a borrower turn: DC-00 NOT_EVALUABLE, AJ-05)",
                           iid)
                else:
                    v.warn("PD015", f"{f.code}: {name}=B{n} speaker {spk} but the rubric role is "
                                    f"{spec_el.get('role')}", iid)
        # attribution in TRANSCRIPT mode (golddrv §7 derivation)
        attr_class = v.raw[f.code].get("attribution_class")
        if UnitMode.TRANSCRIPT in it.unit_modes and attr_class:
            want_attr, _ = derive_attribution(attr_class, AttributionFacts(registered=_registered(f.note)),
                                              "TRANSCRIPT", None)
            if want_attr is not None and want_attr != f.attribution_T:
                v.err("PD012", f"{f.code} attribution (T) {f.attribution_T} but §7 derives {want_attr} "
                               f"(class {attr_class}, registration {'shown' if _registered(f.note) else 'not shown'})",
                      iid)
        # capability: the finding must be evaluable in at least one of the item's modes
        in_scope = []
        for um in it.unit_modes:
            im = UNIT_MODE_INPUT[um].value
            ok, _ = v.table.in_scope(f.code, im, has_call_start_ts=it.has_header and um is not UnitMode.A,
                                     has_timestamps=False, provenance=None)
            in_scope.append(ok)
        if not any(in_scope):
            v.err("PD012", f"{f.code} is OUT_OF_SCOPE in every mode of the item {[m.value for m in it.unit_modes]}",
                  iid)
    for g, st in it.gates.items():
        if st in FIRED and g not in gate_findings:
            v.err("PD005", f"{g} is {st} but has no gate finding (anchor and evidence elements)", iid)
    # ---------------------------------------------------------------- G7 / header / calling window
    g7 = it.gates.get("G7")
    if it.has_header != (g7 in ("PASS", "FAIL")):
        v.err("PD012", f"G7 {g7} with header {'present' if it.has_header else 'absent'} (G7 needs the header "
                       "call_start_ts; without it G7 is OUT_OF_SCOPE)", iid)
    if it.header_time is not None and g7 in ("PASS", "FAIL"):
        win = v.spec.profile["calling_window"]
        start, end = (_hhmm(win[k]) for k in ("start", "end"))
        inside = start <= it.header_time < end
        if inside != (g7 == "PASS"):
            v.err("PD012", f"header time {it.header_time:%H:%M} is {'inside' if inside else 'outside'} the calling "
                           f"window but G7 is {g7}", iid)
    # ---------------------------------------------------------------- DC-00: a borrower-side speaker must exist
    speakers = {b.speaker for b in it.beats.values()}
    if it.evaluability == EvaluabilityStatus.EVALUABLE.value and "C" not in speakers and "T" in speakers:
        v.warn("PD015", "every customer-side beat is a third party (T): the transcript must label that speaker "
                        "BORROWER, otherwise DC-00 finds no borrower turn and the call is NOT_EVALUABLE (AJ-05)", iid)
    if it.tuning_only:
        return
    # ---------------------------------------------------------------- verdict (V3-V5), positive (AJ-09), DW, CL
    fired = [g for g, st in it.gates.items() if st in FIRED]
    majors = [f for f in it.findings if f.code not in GATES and f.severity == "MAJOR"]
    want_verdict = "CRITICAL_FAIL" if fired else "NEEDS_ATTENTION" if majors else "MEETS_BAR"
    if it.verdict not in {x.value for x in Verdict}:
        v.err("PD005", f"verdict {it.verdict!r} unknown", iid)
    elif it.verdict != want_verdict:
        v.err("PD006", f"verdict {it.verdict} but V3-V5 give {want_verdict} (fired {fired}, majors "
                       f"{[f.code for f in majors]})", iid)
    if it.evaluability not in {e.value for e in EvaluabilityStatus}:
        v.err("PD005", f"evaluability {it.evaluability!r} unknown", iid)
    want_pos = reg.outcome_positive([d for d, _ in disps], firm)
    if it.positive != want_pos:
        v.err("PD009", f"positive={it.positive} but the positive-outcome rule gives {want_pos} (AJ-09: PTP_STATED "
                       f"counts only when firm; firmness {firm})", iid)
    if (it.outcome_attribution == "AGENT_DRIVEN") != it.positive:
        v.err("PD009", f"outcome_attribution {it.outcome_attribution} with positive={it.positive} (AGENT_DRIVEN = "
                       "positive outcome, rubric outcome_attribution_rules)", iid)
    if it.positive and fired:
        want_dw = "CRITICAL"
    elif it.positive and any(f.code in reg.dw_inducement_set for f in majors):
        want_dw = "MATERIAL?"  # needs the commitment turn: anchor < commitment_turn (labeler decides)
    else:
        want_dw = "NONE"
    if want_dw == "MATERIAL?":
        if it.dangerous_win not in ("MATERIAL", "NONE"):
            v.err("PD007", f"dangerous_win {it.dangerous_win} but only MATERIAL or NONE are possible", iid)
    elif it.dangerous_win != want_dw:
        v.err("PD007", f"dangerous_win {it.dangerous_win} but the tag rule gives {want_dw} (positive "
                       f"{it.positive}, fired {fired})", iid)
    want_cl = (not it.positive and it.verdict == "MEETS_BAR"
               and it.outcome_attribution in ("CUSTOMER_DRIVEN", "POLICY_DRIVEN"))
    if it.clean_loss != want_cl:
        v.err("PD008", f"clean_loss {it.clean_loss} but the tag rule gives {want_cl}", iid)
    # ---------------------------------------------------------------- targets
    finding_codes = {f.code for f in it.findings}
    for code, negative, _ in it.targets + it.secondary:
        if code not in v.raw:
            v.err("PD005", f"target {code} is not a rubric check", iid)
            continue
        present = code in finding_codes or it.gates.get(code) in FIRED
        if negative and present:
            v.err("PD004", f"negative target {code} is labeled as a defect", iid)
        if not negative and not present:
            v.err("PD004", f"target {code} has no finding / fired gate", iid)
    for g in it.control_target_gates:
        if it.gates.get(g) != "PASS":
            v.warn("PD016", f"control target {g} has gold {it.gates.get(g)}; SD-08 counts a targeted control only "
                            "when gold = PASS, so this must-not-fire target is excluded from targeted false fires "
                            "(it still counts in global false fires)", iid)
    if it.control_target_gates and not any(neg for _, neg, _ in it.targets):
        v.err("PD004", "control target gates on an item without a negative target", iid)


def _check_pairs(v: _V, items: dict[str, DesignItem], frozen: FrozenDev, matrix: dict[str, dict[str, str]]) -> None:
    by_pair: dict[str, list[DesignItem]] = {}
    for it in items.values():
        if it.pair:
            by_pair.setdefault(it.pair[0], []).append(it)
        if frozen.pairs.get(it.item_id) != it.pair:
            v.err("PD010", f"pair {it.pair} disagrees with frozen-contract §12 {frozen.pairs.get(it.item_id)}",
                  it.item_id)
    for pid, members in sorted(by_pair.items()):
        roles = sorted(m.pair[1] for m in members if m.pair)
        if roles != ["clean", "violating"]:
            v.err("PD010", f"pair {pid} needs exactly one clean and one violating member, got {roles}")
            continue
        clean = next(m for m in members if m.pair and m.pair[1] == "clean")
        viol = next(m for m in members if m.pair and m.pair[1] == "violating")
        for what in ("split", "pack", "language", "unit_modes"):
            if getattr(clean, what) != getattr(viol, what):
                v.err("PD010", f"pair {pid}: {what} differs ({getattr(clean, what)} vs {getattr(viol, what)})")
        vt = [c for c, neg, _ in viol.targets if not neg]
        ct = [c for c, neg, _ in clean.targets if neg]
        if not vt or vt[0] not in ct:
            v.err("PD010", f"pair {pid}: the clean member's negative targets {ct} do not include the violating "
                           f"target {vt}")
            continue
        target = vt[0]
        if clean.gates is None or viol.gates is None:
            continue
        # non-target gate differences: declared ones are named in the risk notes
        declared = matrix[viol.item_id]["benchmark_risk_notes"] + " " + matrix[clean.item_id]["benchmark_risk_notes"]
        for g in GATES:
            if g != target and clean.gates[g] != viol.gates[g] and g not in declared:
                why = " (the header differs: one member carries call_start_ts)" if g == "G7" else ""
                v.warn("PD011", f"pair {pid}: {g} differs ({clean.item_id} {clean.gates[g]} vs {viol.item_id} "
                                f"{viol.gates[g]}){why}; undeclared non-target difference (authoring constraint 4)")
