"""Evaluator isolation from benchmark identity (dev ingestion, Stage 5).

What a system (K0 / A / A+ / B) receives is the NormalizedInput and an EvaluationContext. Neither may expose a
semantic item id (C-08 says "critical", K-07 says "control"), a file name, a split / pack or any case-card or gold
content. Items here are temporary directories named after real DEV ids, holding obviously synthetic stub turns.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

import factories as F
from ignosis_eval.benchmark.layout import BenchLayout
from ignosis_eval.benchmark.public_dev import validate_public_dev
from ignosis_eval.contracts.benchmark import ItemMeta
from ignosis_eval.contracts.canonical_input import AudioRef, NormalizedInput
from ignosis_eval.contracts.enums import UnitMode
from ignosis_eval.contracts.gold_label import GoldProvenance
from ignosis_eval.evaluators.base import EvaluationContext, TraceSink
from ignosis_eval.evaluators.mock_llm import ReplayLLMClient
from ignosis_eval.evaluators.pipelines import APlusDeriver, EvaluatorA, EvaluatorB, a_final_raw_output
from ignosis_eval.integrity.guard import ProtectedPathGuard, ProtectedPathViolation
from ignosis_eval.pipeline.asr import ReplayASR
from ignosis_eval.pipeline.normalize import NormalizationError, build_normalized_input

SP = F.spec()
PKG = F.REPO / "src" / "ignosis_eval"
DESIGN = validate_public_dev(F.REPO / "bench" / "public", SP).design
assert DESIGN is not None
DESIGN_IDS = sorted(DESIGN.items)
PAIR_IDS = [p.pair_id for p in DESIGN.registries().pairs]
NO_SLEEP = lambda s: None  # noqa: E731


def _item(tmp_path: Path, item_id: str, *, text: str | None = None, audio: bool = False,
          name: str | None = None) -> tuple[ItemMeta, Path]:
    """A temporary item directory named after `item_id`; artifact files are named after the id on purpose."""
    d = tmp_path / "items" / item_id
    d.mkdir(parents=True)
    fname = f"{name or item_id}.txt"
    (d / fname).write_text(text or F.stub_transcript(), encoding="utf-8")
    arts = {"transcript": fname}
    modes = ["TRANSCRIPT"]
    if audio:
        (d / f"{name or item_id}.wav").write_bytes(b"RIFF-synthetic-stub")
        arts["audio"] = f"{name or item_id}.wav"
        modes = ["TRANSCRIPT", "A"]
    meta = ItemMeta.model_validate({"item_id": item_id, "split": "dev", "pack": "core", "language": "hi-en",
                                    "unit_modes": modes, "artifacts": arts})
    return meta, d


def _forbidden(tmp_path: Path, *extra: str) -> list[str]:
    return DESIGN_IDS + PAIR_IDS + [str(tmp_path), "items/", ".txt", ".wav", "bench/", "dev-case-cards",
                                    "gold-blueprint", "master-matrix", *extra]


def _leaks(blob: str, tokens: list[str]) -> list[str]:
    out = []
    for t in tokens:
        if re.search(rf"(?<![A-Za-z0-9-]){re.escape(t)}(?![A-Za-z0-9-])", blob) if re.match(r"^[A-Z]", t) \
                else t in blob:
            out.append(t)
    return out


# ================================================================================ opaque aliases
def test_evaluator_inputs_receive_opaque_aliases(tmp_path):
    ni_c, ni_k = (build_normalized_input(*_item(tmp_path, iid), UnitMode.TRANSCRIPT, SP) for iid in ("C-08", "K-07"))
    for ni in (ni_c, ni_k):
        assert ni.input_alias is not None and re.fullmatch(r"in-[0-9a-f]{16}", ni.input_alias)
    # the alias is a function of the content only: two items with identical stub content are indistinguishable
    assert ni_c.input_alias == ni_k.input_alias and ni_c.to_json_dict() == ni_k.to_json_dict()
    blob = json.dumps(ni_c.to_json_dict())
    assert _leaks(blob, _forbidden(tmp_path)) == []


def test_alias_cannot_carry_an_identifier():
    ni = F.make_ni(SP)
    body = ni.to_json_dict()
    for forged in ("C-08", "in-" + "0" * 16, "in-" + hashlib.sha256(b"C-08").hexdigest()[:16]):
        with pytest.raises(ValidationError):
            NormalizedInput.model_validate({**body, "input_alias": forged})
    assert NormalizedInput.model_validate(body).input_alias == ni.input_alias


def test_normalized_input_schema_has_no_identifier_fields():
    schema = json.loads((F.REPO / "schemas" / "normalized_input.schema.json").read_text(encoding="utf-8"))
    names: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            names.update(o.get("properties", {}))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(schema)
    banned = {"path", "file", "filename", "item_id", "unit_id", "item", "split", "pack", "case_card", "card",
              "gold", "target_check", "pair", "pair_id", "category", "scenario"}
    assert not names & banned, names & banned
    assert set(AudioRef.model_fields) == {"sha256", "format"}
    assert [f.name for f in dataclasses.fields(EvaluationContext)] == ["repetition", "spec", "trace"]


def test_audio_file_name_never_reaches_the_system(tmp_path):
    meta, d = _item(tmp_path, "C-12", audio=True)
    asr = ReplayASR(engine="stub", version="0", cache_dir=tmp_path / "cache")
    sha = hashlib.sha256((d / "C-12.wav").read_bytes()).hexdigest()
    ReplayASR.write_cache_entry(tmp_path / "cache", asr, sha, [
        {"role": "AGENT", "text": "stub asr alpha", "start_s": 0.0, "end_s": 1.0},
        {"role": "BORROWER", "text": "stub asr beta", "start_s": 1.2, "end_s": 2.0}], mapping_confidence=0.97)
    ni = build_normalized_input(meta, d, UnitMode.A, SP, asr)
    assert ni.audio is not None and ni.audio.sha256 == sha
    assert _leaks(json.dumps(ni.to_json_dict()), _forbidden(tmp_path, "C-12")) == []


# ================================================================================ ids cannot reach prompts
def test_case_ids_cannot_leak_into_prompts(tmp_path):
    ni = build_normalized_input(*_item(tmp_path, "C-08"), UnitMode.TRANSCRIPT, SP)
    F.write_replay(tmp_path / "replay", "a_evaluate", ni, [F.body_json(F.record())])
    F.write_replay(tmp_path / "replay", "b_extract", ni, [json.dumps({"events": []})])
    client = ReplayLLMClient(tmp_path / "replay")
    ctx_a = EvaluationContext(repetition=1, spec=SP, trace=TraceSink())
    rec = EvaluatorA(client, SP, sleep=NO_SLEEP).evaluate(ni, ctx_a)
    ctx_b = EvaluationContext(repetition=1, spec=SP, trace=TraceSink())
    with pytest.raises(NotImplementedError):  # B's rule engine is the next phase; the extraction call still ran
        EvaluatorB(client, SP, sleep=NO_SLEEP).evaluate(ni, ctx_b)
    requests = ctx_a.trace.requests + ctx_b.trace.requests
    assert {r["task"] for r in requests} == {"a_evaluate", "b_extract"}
    prompts = json.dumps(requests)
    assert _leaks(prompts, _forbidden(tmp_path)) == []
    assert "stub agent line alpha" in prompts  # the prompt does carry the (synthetic) content
    aplus, log = APlusDeriver(SP).derive(rec, a_final_raw_output(ctx_a.trace.responses), ni, SP)
    outputs = json.dumps([rec.to_json_dict(), aplus.to_json_dict(), log.entries])
    assert _leaks(outputs, _forbidden(tmp_path)) == []


@pytest.mark.parametrize("line", ["stub agent line C-08", "stub agent line c-08", "stub agent line MP-10",
                                  "stub agent line c08_final"])
def test_identifier_in_the_evaluated_text_fails_closed(tmp_path, line):
    turns = F.STUB_TURNS[:2] + (("AGENT", line),) + F.STUB_TURNS[3:]
    meta, d = _item(tmp_path, "C-08", text=F.stub_transcript(turns), name="c08_final")
    with pytest.raises(NormalizationError, match="identifier"):
        build_normalized_input(meta, d, UnitMode.TRANSCRIPT, SP)


# ================================================================================ gold stays separate
def test_design_and_gold_are_protected_from_evaluators():
    layout = BenchLayout(F.REPO / "bench")
    protected = layout.protected_paths()
    assert layout.public_dir in protected and layout.dev_dir / "gold" in protected
    with ProtectedPathGuard(protected):
        for p in (layout.public_dir / "dev-gold-blueprint.yaml", layout.public_dir / "dev-case-cards.md",
                  layout.public_dir / "dev-master-matrix.csv"):
            with pytest.raises(ProtectedPathViolation):
                p.read_bytes()
        with pytest.raises(ProtectedPathViolation):
            list(layout.public_dir.iterdir())


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_design_never_meets_evaluator_code():
    for sub in ("evaluators", "engine", "pipeline"):  # evaluator side never imports the design / gold
        for py in (PKG / sub).rglob("*.py"):
            bad = [m for m in _imports(py) if m.startswith(("ignosis_eval.benchmark", "ignosis_eval.contracts.gold"))]
            assert not bad, f"{py.name}: {bad}"
    # and the design validator never reads evaluator output (runs, records, traces)
    bad = [m for m in _imports(PKG / "benchmark" / "public_dev.py")
           if m.startswith(("ignosis_eval.evaluators", "ignosis_eval.engine", "ignosis_eval.runner",
                            "ignosis_eval.scoring", "ignosis_eval.metrics"))]
    assert not bad, bad


def test_blueprint_is_never_turned_into_gold(tmp_path):
    """Validation reads the design and writes nothing: no gold labels, registries or cards are derived from it."""
    bench = F.REPO / "bench"
    before = sorted(str(p.relative_to(bench)) for p in bench.rglob("*"))
    from ignosis_eval.benchmark.checks import check_bench

    check_bench(BenchLayout(bench), SP, scopes=("dev",), require_gold=True)
    after = sorted(str(p.relative_to(bench)) for p in bench.rglob("*"))
    assert before == after
    assert not list((bench / "dev" / "gold").glob("*.gold.json"))
    assert json.loads((bench / "registries.json").read_text(encoding="utf-8"))["pairs"] == []
    assert GoldProvenance.model_fields["derived_from_evaluator_output"].annotation.__args__ == (False,)
