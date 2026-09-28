"""Evaluator interfaces: shared contract, gate precedence in aggregation, retries, determinism, backends."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FIXTURE_ROOT
from factories import PROFILE, PROFILE_SHA, make_input
from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.enums import (
    AttributionTarget,
    EvaluabilityStatus,
    EvidenceModality,
    GateStatus,
    InputMode,
    OutcomeCode,
    RoutingDecision,
    Speaker,
    TranscriptSource,
    Verdict,
)
from ignosis_eval.contracts.evaluation_record import EvaluatorInfo
from ignosis_eval.contracts.io import read_json
from ignosis_eval.contracts.record_checks import check_modality_conformance, check_record_integrity
from ignosis_eval.evaluators.base import EvaluationContext
from ignosis_eval.evaluators.builder import RecordBuilder
from ignosis_eval.evaluators.judgement import EvaluatorOutputError
from ignosis_eval.evaluators.llm import LLMClient, LLMError, LLMResponse
from ignosis_eval.evaluators.mock_llm import MockLLMClient
from ignosis_eval.evaluators.pipelines import EvaluatorA, EvaluatorAPlus, EvaluatorB
from ignosis_eval.evaluators.registry import EVALUATOR_NAMES, build_evaluator
from ignosis_eval.pipeline.asr import MockASR
from ignosis_eval.pipeline.normalize import NormalizationError, normalize_input
from ignosis_eval.contracts.run_manifest import RetryPolicy

INFO = EvaluatorInfo(name="t", version="0", architecture="MOCK")


def _ctx(seed: int = 7, rep: int = 1) -> EvaluationContext:
    return EvaluationContext(rep, seed, PROFILE, PROFILE_SHA)


def _fixture_inputs():
    out = []
    for p in sorted((FIXTURE_ROOT / "dataset").glob("*/*/input.json")):
        inp = CanonicalInput.model_validate(read_json(p))
        out.append((p.parent.name, normalize_input(inp, p.parent, MockASR())))
    return out


FIXTURE_INPUTS = _fixture_inputs()


@pytest.mark.parametrize("name", EVALUATOR_NAMES)
def test_all_evaluators_emit_the_same_valid_contract(name):
    ev = build_evaluator(name)
    modes = set()
    for case_id, inp in FIXTURE_INPUTS:
        rec = ev.evaluate(inp, _ctx())
        assert rec.call_id == inp.call_id and rec.input_mode is inp.input_mode
        assert check_record_integrity(rec, inp, PROFILE) == [], case_id
        assert check_modality_conformance(rec, inp, PROFILE) == [], case_id
        assert rec.experiment is None  # attached by the runner, never by the evaluator
        modes.add(inp.input_mode)
    assert modes == set(InputMode)  # audio only, transcript only, audio + transcript all exercised


def test_run_config_records_prompt_hashes_model_and_retry():
    rc = build_evaluator("b").run_config()
    assert set(rc.prompt_hashes) == {"b_gate_check", "b_defect_scan", "b_outcome"}
    assert rc.llm_backend == "mock" and rc.model_id == "mock-llm/0" and rc.temperature == 0.0
    assert rc.retry_policy.max_attempts == 3
    k0 = build_evaluator("k0").run_config()
    assert k0.llm_backend == "none" and k0.model_id is None and k0.prompt_hashes == {}


# ------------------------------------------------------------------------------------------ aggregation
def _builder(inp=None):
    return RecordBuilder(INFO, inp or make_input(), PROFILE, PROFILE_SHA, 1)


def test_gate_precedence_overrides_inconclusive():
    b = _builder()
    b.set_evaluability(EvaluabilityStatus.INCONCLUSIVE, ["truncated"])
    b.set_gate("G_AGENT_DISCLOSURE", GateStatus.INCONCLUSIVE)
    e = b.add_evidence(EvidenceModality.TRANSCRIPT, ["t03"])
    b.set_gate("G_NO_THREATS_OR_ABUSE", GateStatus.FAIL, [e])
    rec = b.build(0.9)
    assert rec.verdict is Verdict.FAIL and rec.evaluability.status is EvaluabilityStatus.EVALUABLE
    assert rec.routing.decision is RoutingDecision.COMPLIANCE_ESCALATION
    assert check_record_integrity(rec, make_input(), PROFILE) == []


def test_inconclusive_gate_yields_inconclusive_verdict():
    b = _builder()
    b.set_gate("G_AGENT_DISCLOSURE", GateStatus.INCONCLUSIVE)
    b.set_gate("G_NO_THREATS_OR_ABUSE", GateStatus.PASS)
    rec = b.build(0.9)
    assert rec.verdict is Verdict.INCONCLUSIVE and rec.routing.decision is RoutingDecision.HUMAN_REVIEW
    assert rec.dangerous_win is None and rec.clean_loss is None


def test_out_of_scope_drops_gates_and_findings():
    b = _builder()
    b.set_evaluability(EvaluabilityStatus.OUT_OF_SCOPE, ["not collections"])
    e = b.add_evidence(EvidenceModality.TRANSCRIPT, ["t01"])
    b.set_gate("G_NO_THREATS_OR_ABUSE", GateStatus.FAIL, [e])
    b.add_finding("DEF_THREAT_OR_INTIMIDATION", [e], AttributionTarget.AGENT_LOGIC)
    rec = b.build(0.9)
    assert rec.verdict is Verdict.OUT_OF_SCOPE and rec.gates == [] and rec.findings == []
    assert rec.routing.decision is RoutingDecision.NO_ACTION
    assert check_record_integrity(rec, make_input(), PROFILE) == []


def test_asr_attributed_major_does_not_fail_agent_and_dw_cl():
    b = _builder()
    e = b.add_evidence(EvidenceModality.TRANSCRIPT, ["t03"])
    b.add_finding("DEF_INCONSISTENT_AMOUNT", [e], AttributionTarget.ASR)
    b.set_outcome([OutcomeCode.PROMISE_TO_PAY])
    rec = b.build(0.9)
    assert rec.verdict is Verdict.PASS and rec.dangerous_win is False
    b2 = _builder()
    b2.add_finding("DEF_INCONSISTENT_AMOUNT", [b2.add_evidence(EvidenceModality.TRANSCRIPT, ["t03"])],
                   AttributionTarget.AGENT_LOGIC)
    b2.set_outcome([OutcomeCode.PROMISE_TO_PAY])
    rec2 = b2.build(0.9)
    assert rec2.verdict is Verdict.FAIL and rec2.dangerous_win is True and rec2.clean_loss is False
    b3 = _builder()
    b3.set_outcome([OutcomeCode.REFUSAL_TO_PAY])
    rec3 = b3.build(0.9)
    assert rec3.verdict is Verdict.PASS and rec3.clean_loss is True


def test_low_confidence_pass_is_routed_to_review_and_unknown_defect_dropped():
    b = _builder()
    b.add_finding("DEF_NOT_IN_PROFILE", [], AttributionTarget.AGENT_LOGIC)
    rec = b.build(0.3)
    assert rec.routing.decision is RoutingDecision.HUMAN_REVIEW and rec.findings == []
    assert any("unknown to profile" in w for w in rec.warnings)


# ------------------------------------------------------------------------------------------ modality
def test_transcript_only_without_call_start_ts_is_conformant():
    inp = make_input(call_start_ts=None)
    rec = build_evaluator("a").evaluate(inp, _ctx())
    assert rec.gate("G_CONTACT_HOURS").status is GateStatus.INCONCLUSIVE
    assert check_modality_conformance(rec, inp, PROFILE) == []


def test_audio_only_normalization():
    inp = CanonicalInput.model_validate(read_json(FIXTURE_ROOT / "dataset/dev/fx-0007/input.json"))
    case_dir = FIXTURE_ROOT / "dataset/dev/fx-0007"
    n = normalize_input(inp, case_dir, MockASR())
    assert n.transcript.provenance.source is TranscriptSource.PIPELINE_ASR
    assert n.transcript.provenance.derived_from_audio_sha256 == inp.audio.sha256
    assert n.evidence_availability.transcript and n.input_mode is InputMode.AUDIO_ONLY
    assert normalize_input(inp, case_dir, MockASR()) == n  # deterministic
    silent = CanonicalInput.model_validate(read_json(FIXTURE_ROOT / "dataset/dev/fx-0006/input.json"))
    n2 = normalize_input(silent, FIXTURE_ROOT / "dataset/dev/fx-0006", MockASR())
    assert n2.transcript is None and "asr_failed" in n2.evaluability.known_issues
    with pytest.raises(NormalizationError, match="no ASR"):
        normalize_input(inp, case_dir, None)


def test_normalization_rejects_tampered_audio(tmp_path):
    import shutil
    d = tmp_path / "fx-0007"
    shutil.copytree(FIXTURE_ROOT / "dataset/dev/fx-0007", d)
    (d / "audio.wav").write_bytes(b"tampered")
    inp = CanonicalInput.model_validate(read_json(d / "input.json"))
    with pytest.raises(NormalizationError, match="sha256 mismatch"):
        normalize_input(inp, d, MockASR())


# ------------------------------------------------------------------------------------------ determinism / noise
def test_same_seed_same_record_and_noise_varies_with_seed():
    inp = dict(FIXTURE_INPUTS)["fx-0002"]
    ev = EvaluatorA(MockLLMClient(noise_rate=0.5))
    h1 = ev.evaluate(inp, _ctx(seed=1)).content_hash()
    assert ev.evaluate(inp, _ctx(seed=1)).content_hash() == h1
    hashes = {ev.evaluate(inp, _ctx(seed=s)).content_hash() for s in range(20)}
    assert len(hashes) > 1


# ------------------------------------------------------------------------------------------ retries / failures
def test_retry_policy_and_trace():
    ctx = _ctx()
    ev = EvaluatorA(MockLLMClient(transient_failures=2), sleep=lambda s: None)
    rec = ev.evaluate(dict(FIXTURE_INPUTS)["fx-0001"], ctx)
    assert rec.verdict is Verdict.PASS
    assert ctx.trace.usage["llm_calls"] == 1 and ctx.trace.usage["llm_attempts"] == 3
    assert ctx.trace.usage["retries"] == 2
    assert [r["ok"] for r in ctx.trace.responses] == [False, False, True]
    assert "payload" not in ctx.trace.requests[0]["metadata"]
    assert "payload_sha256" in ctx.trace.requests[0]["metadata"]
    ev_fail = EvaluatorA(MockLLMClient(transient_failures=5), sleep=lambda s: None,
                         retry_policy=RetryPolicy(max_attempts=2, backoff_initial_s=0, backoff_multiplier=1))
    with pytest.raises(LLMError):
        ev_fail.evaluate(dict(FIXTURE_INPUTS)["fx-0001"], _ctx())


class _Garbage(LLMClient):
    backend, model_id = "mock", "garbage"

    def complete(self, request):
        return LLMResponse(request.request_id, self.model_id, "I think this call is fine!")


def test_malformed_model_output_raises():
    with pytest.raises(EvaluatorOutputError):
        EvaluatorA(_Garbage()).evaluate(dict(FIXTURE_INPUTS)["fx-0001"], _ctx())


def test_a_plus_drops_ungrounded_findings():
    class Fabricating(MockLLMClient):
        def _task_holistic_judge(self, inp, payload, rng):
            j = super()._task_holistic_judge(inp, payload, rng)
            j["findings"].append({"defect_id": "DEF_ABUSIVE_LANGUAGE", "attribution": "agent_logic",
                                  "evidence": [{"modality": "transcript", "turn_ids": ["t01"],
                                                "quote": "you are an idiot"}]})
            return j

    inp = dict(FIXTURE_INPUTS)["fx-0001"]
    a = EvaluatorA(Fabricating()).evaluate(inp, _ctx())
    ap = EvaluatorAPlus(Fabricating()).evaluate(inp, _ctx())
    assert any(f.defect_id == "DEF_ABUSIVE_LANGUAGE" for f in a.findings)
    assert not any(f.defect_id == "DEF_ABUSIVE_LANGUAGE" for f in ap.findings)


def test_b_makes_one_call_per_gate():
    ctx = _ctx()
    EvaluatorB(MockLLMClient()).evaluate(dict(FIXTURE_INPUTS)["fx-0001"], ctx)
    tasks = [r["task"] for r in ctx.trace.requests]
    assert tasks.count("gate_check") == len(PROFILE.gates)
    assert tasks.count("defect_scan") == 1 and tasks.count("outcome_extract") == 1


def test_real_backend_is_not_wired_and_unknown_names_rejected():
    with pytest.raises(NotImplementedError):
        build_evaluator("a", llm_backend="anthropic", model_id="some-model")
    with pytest.raises(ValueError):
        build_evaluator("c")
    with pytest.raises(ValueError):
        build_evaluator("k0", options={"noise_rate": 0.1})


def test_heuristics_ignore_customer_prompt_injection():
    inp = dict(FIXTURE_INPUTS)["fx-0009"]
    turn = inp.transcript.turns[3]
    assert turn.speaker is Speaker.CUSTOMER and "ignore previous instructions" in turn.text
    rec = build_evaluator("a").evaluate(inp, _ctx())
    assert rec.verdict is Verdict.FAIL


def test_prompts_are_marked_as_unoptimized_stubs():
    from ignosis_eval.evaluators.prompts import PROMPT_DIR
    for p in PROMPT_DIR.glob("*.md"):
        assert "UNOPTIMIZED STUB PROMPT" in Path(p).read_text()
