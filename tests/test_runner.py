"""Runner protocol (P-4 … P-11), lock (P-8 / H7), blinding (P-10) and append-only storage, on synthetic stubs
with the replay LLM mock. These runs measure plumbing only."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import factories as F
from ignosis_eval.benchmark.holdout import LockedRunEntry, LockedRunError, register_locked_run
from ignosis_eval.contracts.benchmark import ItemMeta
from ignosis_eval.contracts.enums import Split, System
from ignosis_eval.contracts.io import read_json
from ignosis_eval.evaluators.k0 import KeywordFloorK0
from ignosis_eval.evaluators.mock_llm import ReplayLLMClient
from ignosis_eval.evaluators.pipelines import APlusDeriver, EvaluatorA, EvaluatorB
from ignosis_eval.integrity.freeze import IntegrityError
from ignosis_eval.pipeline.normalize import build_normalized_input
from ignosis_eval.contracts.unit_alias import payload_violations
from ignosis_eval.runner.aliases import (
    ITEM_ID_PATTERN,
    OpaqueAliasViolation,
    load_unit_alias_mapping,
    prerun_payload_check,
)
from ignosis_eval.runner.blind import BlindingError, build_view, reveal
from ignosis_eval.runner.experiment import RunConfig, arch_order, run_experiment, seeded_order
from ignosis_eval.runner.lock import LockError
from ignosis_eval.runner.storage import AppendOnlyError, ResultsLayout, write_json_once
from ignosis_eval.scoring.loader import ScoringError
from ignosis_eval.scoring.scorer import score_view
from test_evaluators import StubRuleEngine

ITEMS = {"ZZ-R01": F.STUB_TURNS, "ZZ-R02": F.STUB_TURNS[:4]}
NO_SLEEP = lambda s: None  # noqa: E731


def _setup(layout, spec, tmp_path, *, a_entries=None, b=False):
    for iid, turns in ITEMS.items():
        gl = F.gold(iid, gates={"G3": {"status": "FAIL", "anchor_turns": [3]}}) if iid == "ZZ-R01" else None
        F.add_item(layout, iid, transcript=F.stub_transcript(turns), gold_label=gl)
    F.freeze(layout, spec)
    replay = tmp_path / "replay"
    for iid in ITEMS:
        ip = layout.item_paths(Split.DEV, iid)
        meta = ItemMeta.model_validate(read_json(ip.meta_path))
        ni = build_normalized_input(meta, ip.item_dir, meta.unit_modes[0], spec)
        rec = F.record(gates={"G3": F.gate("G3", "FAIL", evidence=[F.ev(3, "stub agent line gamma")])}) \
            if iid == "ZZ-R01" else F.record()
        F.write_replay(replay, "a_evaluate", ni, a_entries if a_entries is not None else [F.body_json(rec)])
        if b:
            F.write_replay(replay, "b_extract", ni, ['{"events": []}'])
    return replay


def _cfg(layout, tmp_path, replay, systems, **kw):
    return RunConfig(bench_root=layout.root, private_root=layout.private_root, split=Split.DEV, systems=systems,
                     base_seed=11, results_root=tmp_path / "results", replay_dir=replay, **kw)


def _systems(spec, replay, rule_engine=None):
    client = ReplayLLMClient(replay)
    return {System.K0: KeywordFloorK0(), System.A: EvaluatorA(client, spec, sleep=NO_SLEEP),
            System.A_PLUS: APlusDeriver(spec),
            System.B: EvaluatorB(client, spec, rule_engine=rule_engine or StubRuleEngine(), sleep=NO_SLEEP)}


# ------------------------------------------------------------------------------------------ P-6
def test_p6_ordering():
    units = ["u3", "u1", "u2", "u4"]
    assert seeded_order(units, 5, 1) == seeded_order(list(reversed(units)), 5, 1)  # depends on the set only
    assert [seeded_order(units, 5, r) for r in (1, 2, 3)] != [seeded_order(units, 5, 1)] * 3
    all_s = [System.K0, System.A, System.A_PLUS, System.B]
    assert [arch_order(all_s, r) for r in (1, 2, 3)] == [[System.A, System.B], [System.B, System.A],
                                                         [System.A, System.B]]


class Spy:
    def __init__(self, inner, log, name):
        self.inner, self.log, self.name = inner, log, name

    def config(self):
        return self.inner.config()

    def system_info(self):
        return self.inner.system_info()

    def evaluate(self, ni, ctx):
        self.log.append((self.name, ni.turns[-1].text, ctx.repetition))
        return self.inner.evaluate(ni, ctx)

    def derive(self, *a, **kw):
        self.log.append(("A+",))
        return self.inner.derive(*a, **kw)


def test_run_protocol_order(layout, spec, tmp_path):
    replay = _setup(layout, spec, tmp_path, b=True)
    log: list = []
    base = _systems(spec, replay)
    systems = {s: Spy(base[s], log, s.value) for s in base}
    systems[System.A_PLUS].__class__ = type("SpyAPlus", (Spy, APlusDeriver), {})
    systems[System.A_PLUS].__init__(base[System.A_PLUS], log, "A+")
    cfg = _cfg(layout, tmp_path, replay, [System.K0, System.A, System.A_PLUS, System.B], repetitions=2)
    run_experiment(cfg, systems_override=systems)
    assert [e[0] for e in log[:2]] == ["K0", "K0"] and all(e[2] == 1 for e in log[:2])  # K0 once, before rep 1
    rest = log[2:]
    # rep 1: A, A+, B per unit; rep 2: B, A, A+ per unit (rotate([A, B], r-1); A+ right after A)
    assert [e[0] for e in rest[:3]] == ["A", "A+", "B"] and [e[0] for e in rest[6:9]] == ["B", "A", "A+"]
    assert len(rest) == 2 * 2 * 3


def test_dev_run_blind_score_reveal(layout, spec, tmp_path):
    replay = _setup(layout, spec, tmp_path)
    res = run_experiment(_cfg(layout, tmp_path, replay, [System.K0, System.A, System.A_PLUS]))
    run_dir = res.run_dir
    man = read_json(run_dir / "manifest.json")
    assert man["kind"] == "dev" and man["locked"] is False and man["repetitions"] == 5 and man["base_seed"] == 11
    assert man["pending_signoff"] and man["price_snapshot"]["status"] == "pending_signoff"
    assert any("DC-02" in s for s in man["frontend_steps_not_implemented"])
    assert sorted(p.name for p in (run_dir / "K0" / "ZZ-R01__TRANSCRIPT").iterdir()) == ["rep_1"]
    assert len(list((run_dir / "A" / "ZZ-R01__TRANSCRIPT").iterdir())) == 5
    rep = run_dir / "A+" / "ZZ-R01__TRANSCRIPT" / "rep_3"
    assert (rep / "derivation_log.json").exists() and read_json(rep / "usage.json")["llm_calls"] == 0
    for f in ("normalized_input.json", "llm_requests.jsonl", "llm_responses.jsonl", "evaluation_record.json",
              "timing.json", "usage.json", "errors.json"):
        assert (run_dir / "A" / "ZZ-R02__TRANSCRIPT" / "rep_1" / f).exists()
    assert res.completion.status == "completed" and res.completion.n_records_written == 2 + 2 * 5 * 2

    results = ResultsLayout(tmp_path / "results")
    assert not (run_dir / "alias_mapping.json").exists() and (results.blinding / res.run_id / "alias_mapping.json").exists()
    # P-17: the unit alias mapping is private (blinding dir + BENCH_PRIVATE_DIR), hashed into the manifest
    mapping = load_unit_alias_mapping(results, res.run_id, man["unit_alias_mapping_sha256"])
    assert {v["item_id"] for v in mapping.values()} == {"ZZ-R01", "ZZ-R02"}
    assert all(re.fullmatch(r"u_[0-9a-f]{8}", a) for a in mapping) and man["p17_payload_strings_checked"] > 0
    assert read_json(layout.private_root / "run_aliases" / f"{res.run_id}.json")["mapping"] == mapping
    ni = read_json(run_dir / "A" / "ZZ-R01__TRANSCRIPT" / "rep_1" / "normalized_input.json")
    assert mapping[ni["unit_alias"]]["item_id"] == "ZZ-R01"
    assert "ZZ-R01" not in json.dumps(ni) and not any(a in json.dumps(man) for a in mapping)  # mapping stays private
    view = build_view(results, res.run_id)
    blob = " ".join(p.read_text() for p in view.rglob("*.json"))
    assert '"system": "K0"' not in blob and '"system": "A+"' not in blob and '"system": "A"' not in blob
    for alias in ("SYS-1", "SYS-2", "SYS-3"):
        assert len(list((view / alias / "ZZ-R01__TRANSCRIPT").iterdir())) == 5  # K0 replicated as reps 1..5
    out = score_view(view, layout, spec, results.scoring)
    metrics = read_json(out / "metrics.json")
    assert any("DEV SPLIT" in w for w in metrics["warnings"]) and any("MOCK" in w for w in metrics["warnings"])
    assert "bench-a1-test-stub, synthetic calls, profile collections_default_v1, rubric 1.2-mvp, model" in \
        metrics["scope_line"]
    assert metrics["unmeasured"]["G7_recall"]["status"] == "UNMEASURED" and "BD-02" in \
        metrics["unmeasured"]["G7_recall"]["reason"]
    assert any("G7 recall is UNMEASURED" in w for w in metrics["warnings"])
    assert set(metrics["systems"]) == {"SYS-1", "SYS-2", "SYS-3"}
    for f in ("item_scores.csv", "metrics.json", "discordance_tables.csv", "human_checks.csv", "scoring_manifest.json"):
        assert (out / f).exists()
    with pytest.raises(ScoringError, match="write-once"):
        score_view(view, layout, spec, results.scoring)
    assert score_view(view, layout, spec, results.scoring, suffix="rescore").name.endswith("__rescore")
    with pytest.raises(BlindingError):
        reveal(results, res.run_id, out.name, "0" * 64)
    from ignosis_eval.integrity.hashing import file_canonical_sha256

    rv = read_json(reveal(results, res.run_id, out.name, file_canonical_sha256(out / "scoring_manifest.json")))
    assert set(rv["alias_to_system"].values()) == {"K0", "A", "A+"}


def test_view_tamper_and_failed_run_refused(layout, spec, tmp_path):
    replay = _setup(layout, spec, tmp_path)
    res = run_experiment(_cfg(layout, tmp_path, replay, [System.K0]))
    results = ResultsLayout(tmp_path / "results")
    view = build_view(results, res.run_id)
    victim = next(view.rglob("evaluation_record.json"))
    victim.chmod(0o644)
    victim.write_text(victim.read_text().replace("MEETS_BAR", "NEEDS_ATTENTION"))
    with pytest.raises(ScoringError, match="hash mismatch"):
        score_view(view, layout, spec, results.scoring)
    with pytest.raises(AppendOnlyError):
        build_view(results, res.run_id)


def test_transport_exhaustion_is_evaluation_failed(layout, spec, tmp_path):
    replay = _setup(layout, spec, tmp_path, a_entries=[{"transport_error": True}] * 4 + ["{}"])
    systems = _systems(spec, replay)
    res = run_experiment(_cfg(layout, tmp_path, replay, [System.A, System.A_PLUS], repetitions=1),
                         systems_override=systems)
    a = read_json(res.run_dir / "A" / "ZZ-R01__TRANSCRIPT" / "rep_1" / "evaluation_record.json")
    ap = read_json(res.run_dir / "A+" / "ZZ-R01__TRANSCRIPT" / "rep_1" / "evaluation_record.json")
    assert a["record_status"] == ap["record_status"] == "EVALUATION_FAILED" and a["verdict"] is None
    assert res.completion.n_evaluation_failed == 4
    errs = read_json(res.run_dir / "A" / "ZZ-R01__TRANSCRIPT" / "rep_1" / "errors.json")
    assert errs[0]["stage"] == "llm_transport"


def test_infrastructure_error_fails_run_closed(layout, spec, tmp_path):
    _setup(layout, spec, tmp_path)
    with pytest.raises(IntegrityError, match="FAILED"):
        run_experiment(_cfg(layout, tmp_path, tmp_path / "empty_replay", [System.A]))
    results = ResultsLayout(tmp_path / "results")
    run_id = next(p.name for p in results.runs.iterdir() if p.is_dir())
    assert read_json(results.runs / run_id / "completion.json")["status"] == "failed"
    with pytest.raises(BlindingError):
        build_view(results, run_id)


class GoldPeeker(KeywordFloorK0):
    target: Path

    def evaluate(self, ni, ctx):
        self.target.read_text()
        return super().evaluate(ni, ctx)


def test_guard_violation_fails_run(layout, spec, tmp_path):
    replay = _setup(layout, spec, tmp_path)
    peek = GoldPeeker()
    peek.target = layout.gold_dir("dev") / "ZZ-R01.gold.json"
    with pytest.raises(IntegrityError, match="forbidden operation"):
        run_experiment(_cfg(layout, tmp_path, replay, [System.K0]), systems_override={System.K0: peek})


def test_reproducible_records(layout, spec, tmp_path):
    replay = _setup(layout, spec, tmp_path)
    hashes, aliases = [], []
    for i in range(2):
        cfg = _cfg(layout, tmp_path, replay, [System.K0, System.A, System.A_PLUS], repetitions=2)
        cfg.results_root = tmp_path / f"results{i}"
        res = run_experiment(cfg)
        h = {}
        for p in sorted(res.run_dir.rglob("evaluation_record.json")):
            from ignosis_eval.contracts.evaluation_record import EvaluationRecord

            h[str(p.relative_to(res.run_dir))] = EvaluationRecord.model_validate(read_json(p)).content_hash()
        hashes.append(h)
        aliases.append({read_json(p)["unit_alias"] for p in res.run_dir.rglob("normalized_input.json")})
    assert hashes[0] == hashes[1]
    assert aliases[0].isdisjoint(aliases[1])  # P-17: a fresh random alias per unit per run


P17_LINES = ["stub agent line about the E-mail", "stub agent line X-ray", "stub agent line holdout",
             "stub agent line in any language", "stub agent line for Dev"]


@pytest.mark.parametrize("line", P17_LINES)
def test_p17_words_are_caught_at_authoring_time(layout, spec, line):
    """Bench check B017 applies the P-17 payload test to transcripts, so such an item cannot even be frozen."""
    turns = F.STUB_TURNS[:2] + (("AGENT", line),) + F.STUB_TURNS[3:]
    F.add_item(layout, "ZZ-R01", transcript=F.stub_transcript(turns))
    with pytest.raises(IntegrityError, match="B017"):
        F.freeze(layout, spec)


@pytest.mark.parametrize("line", P17_LINES)
def test_p17_prerun_payload_test_fails_the_run(layout, spec, tmp_path, monkeypatch, line):
    """experiment-protocol P-17 rule 4 in the runner: any evaluator-bound payload matching the item-ID pattern or
    containing a pack/split word fails the run before any system is called (deliberately broad; see
    contracts/unit_alias.py). The leaking payload is injected after the bench froze cleanly."""
    import ignosis_eval.runner.experiment as ex

    replay = _setup(layout, spec, tmp_path)
    real = ex.build_normalized_input

    def leaky(meta, item_dir, mode, sp, asr=None, *, unit_alias=None):
        ni = real(meta, item_dir, mode, sp, asr, unit_alias=unit_alias)
        turns = [t.model_copy(update={"text": line}) if t.turn == 3 else t for t in ni.turns]
        return ni.model_copy(update={"turns": turns})

    monkeypatch.setattr(ex, "build_normalized_input", leaky)
    calls: list = []
    spy = Spy(KeywordFloorK0(), calls, "K0")
    with pytest.raises(OpaqueAliasViolation, match="P-17"):
        run_experiment(_cfg(layout, tmp_path, replay, [System.K0]), systems_override={System.K0: spy})
    assert calls == [] and not (tmp_path / "results" / "runs").exists()


def test_p17_payload_check_scope():
    assert ITEM_ID_PATTERN.pattern == "^(G|M|K|C|R|P|J|X|A|AB|E|S|MI|MC|MD|SN|RT|CAL)-"
    for ok in ("stub agent line alpha", "POL-01b", "SPAN_UNRELIABLE", "A+T-platform", "T-asr",
               "2026-09-28T14:05:00+05:30", "DC-02", "B-06/B-11", "SC-03", "u_7f3a91c2", "score", "device", "micron"):
        assert payload_violations(ok) == [], ok
    for bad in ("G-02", "see AB-06 now", "(MI-G1-01)", "SN-D01", "RT-11", "CAL-01", "TW-01", "CP-01", "dev", "HOLDOUT",
                "core", "Micro", "red team", "redteam", "calibration", "abstention", "modality", "snippet"):
        assert payload_violations(bad), bad


def test_p17_prerun_check_includes_the_units_own_source_names(spec):
    ni = F.make_ni(spec, F.STUB_TURNS[:2] + (("AGENT", "stub agent line zzfile7.txt"),))
    with pytest.raises(OpaqueAliasViolation, match="source identifier"):
        prerun_payload_check({"u": ni}, {"u": ["ZZ-Q01", "zzfile7.txt"]})
    assert prerun_payload_check({"u": F.make_ni(spec)}, {"u": ["ZZ-Q01", "zzfile7.txt"]}) > 0


def test_audio_is_renamed_to_the_alias_before_asr(tmp_path, spec):
    seen: list[str] = []

    class Recorder:
        engine, model, version, params_sha256 = "stub", None, "0", "0" * 64

        def describe(self):
            return {"engine": "stub"}

        def transcribe(self, audio_path, sha):
            seen.append(audio_path.name)
            raise RuntimeError("stop after recording the path")

    d = tmp_path / "items" / "ZZ-A01"
    d.mkdir(parents=True)
    (d / "ZZ-A01.wav").write_bytes(b"RIFF-synthetic-stub")
    meta = ItemMeta.model_validate({"item_id": "ZZ-A01", "split": "dev", "pack": "core", "language": "hi-en",
                                    "unit_modes": ["A"], "artifacts": {"audio": "ZZ-A01.wav"}})
    with pytest.raises(RuntimeError, match="stop after"):
        build_normalized_input(meta, d, meta.unit_modes[0], spec, Recorder(), unit_alias="u_0123abcd")
    assert seen == ["u_0123abcd.wav"]


def test_append_only_storage(tmp_path):
    p = tmp_path / "x.json"
    write_json_once(p, {"a": 1})
    with pytest.raises(AppendOnlyError):
        write_json_once(p, {"a": 2})


def test_private_split_needs_locked_run_and_lock_preconditions(layout, spec, tmp_path, monkeypatch):
    F.add_item(layout, "ZZ-H01", split="holdout")
    F.freeze(layout, spec, scope="private")
    cfg = RunConfig(bench_root=layout.root, private_root=layout.private_root, split=Split.HOLDOUT,
                    systems=[System.K0], base_seed=1, results_root=tmp_path / "results")
    with pytest.raises(LockError, match="locked run"):
        run_experiment(cfg)
    cfg.kind = "locked_holdout"
    with pytest.raises(LockError) as e:
        run_experiment(cfg)
    msg = str(e.value)
    assert "--confirm-holdout" in msg and "tag the evaluator commit" in msg and "PENDING_HUMAN_SIGNOFF" in msg
    assert not (tmp_path / "results" / "runs").exists() or not any((tmp_path / "results" / "runs").iterdir())


def test_locked_run_registry(tmp_path):
    path = tmp_path / "locked_runs.jsonl"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def entry(kind):
        return LockedRunEntry(run_id=f"{kind}-1", kind=kind, evaluator_tag="eval-freeze-v1", commit="a" * 40,
                              run_manifest_sha256="b" * 64, registered_at=now)

    with pytest.raises(LockedRunError, match="follows the holdout run"):
        register_locked_run(path, entry("locked_redteam"))
    register_locked_run(path, entry("locked_holdout"))
    with pytest.raises(LockedRunError, match="one locked run"):
        register_locked_run(path, entry("locked_holdout"))
    register_locked_run(path, entry("locked_redteam"))
    assert len(path.read_text().splitlines()) == 2
    assert json.loads(path.read_text().splitlines()[0])["kind"] == "locked_holdout"
