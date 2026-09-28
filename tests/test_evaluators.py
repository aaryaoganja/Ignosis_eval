"""K0 / A / A+ / B (frozen-contract §11, experiment-protocol P-3/P-4) with the replay mock (B-05 pending)."""

from __future__ import annotations

import json

import pytest

import factories as F
from ignosis_eval.contracts.enums import System
from ignosis_eval.contracts.run_manifest import SystemConfig
from ignosis_eval.evaluators.base import EvaluationContext, TraceSink
from ignosis_eval.evaluators.judgement import ExtractionOutput, RuleEngine, RuleEngineResult
from ignosis_eval.evaluators.k0 import KeywordFloorK0
from ignosis_eval.evaluators.llm import AnthropicLLMClient, LLMUnavailableError
from ignosis_eval.evaluators.mock_llm import ReplayLLMClient
from ignosis_eval.evaluators.pipelines import APlusDeriver, EvaluatorA, EvaluatorB, a_final_raw_output
from ignosis_eval.spec.loader import load_spec

NO_SLEEP = lambda s: None  # noqa: E731


def _a(tmp_path, spec, entries):
    ni = F.make_ni(spec)
    F.write_replay(tmp_path / "replay", "a_evaluate", ni, entries)
    a = EvaluatorA(ReplayLLMClient(tmp_path / "replay"), spec, sleep=NO_SLEEP)
    ctx = EvaluationContext(repetition=1, spec=spec, trace=TraceSink())
    return a, ni, ctx


def _g3_record():
    return F.record(gates={"G3": F.gate("G3", "FAIL", critical_status="CONFIRMED", confidence="HIGH",
                                        evidence=[F.ev(3, "stub agent line gamma")])})


def test_a_schema_invalid_twice_is_evaluation_failed(tmp_path, spec):
    a, ni, ctx = _a(tmp_path, spec, ["not json", "{\"verdict\": \"MAYBE\"}"])
    rec = a.evaluate(ni, ctx)
    assert rec.record_status.value == "EVALUATION_FAILED" and rec.verdict is None
    assert rec.failure.schema_attempts == 2
    assert ctx.trace.usage["llm_calls"] == 2 and ctx.trace.usage["schema_retries"] == 1


def test_a_one_schema_retry_then_ok(tmp_path, spec):
    a, ni, ctx = _a(tmp_path, spec, ["not json", F.body_json(_g3_record())])
    rec = a.evaluate(ni, ctx)
    assert rec.record_status.value == "OK" and rec.gate("G3").status.value == "FAIL"
    assert rec.gate("G7").status.value == "PASS"  # A is raw: nothing post-processed (A+ corrects this)


def test_transport_errors_retried_and_not_counted_as_schema_retries(tmp_path, spec):
    a, ni, ctx = _a(tmp_path, spec, [{"transport_error": True}, {"transport_error": True}, F.body_json(_g3_record())])
    rec = a.evaluate(ni, ctx)
    assert rec.record_status.value == "OK"
    u = ctx.trace.usage
    assert (u["llm_calls"], u["transport_retries"], u["schema_retries"]) == (1, 2, 0)


def test_transport_exhausted_raises(tmp_path, spec):
    a, ni, ctx = _a(tmp_path, spec, [{"transport_error": True}] * 4 + [F.body_json(_g3_record())])
    with pytest.raises(LLMUnavailableError):
        a.evaluate(ni, ctx)


def test_a_plus_derived_without_llm_call(tmp_path, spec):
    a, ni, ctx = _a(tmp_path, spec, [F.body_json(_g3_record())])
    a_rec = a.evaluate(ni, ctx)
    deriver = APlusDeriver(spec)
    assert deriver.config().llm_backend == "none" and deriver.config().derived_from == "A"
    rec, log = deriver.derive(a_rec, a_final_raw_output(ctx.trace.responses), ni, spec)
    assert rec.gate("G7").status.value == "OUT_OF_SCOPE"  # pre-check authoritative (no header)
    g3 = rec.gate("G3")
    assert g3.status.value == "FAIL" and g3.confidence.value == "MEDIUM"  # G3 HIGH needs a lexicon hit
    assert rec.system.system == "A+" and log.entries
    failed, _ = deriver.derive(F.failed(), None, ni, spec)
    assert failed.record_status.value == "EVALUATION_FAILED"


def test_k0_empty_lexicon_fires_nothing(spec):
    rec = KeywordFloorK0().evaluate(F.make_ni(spec), EvaluationContext(repetition=1, spec=spec))
    assert rec.record_status.value == "OK" and rec.verdict.value == "MEETS_BAR"
    assert {g.gate.value for g in rec.gates if g.status.value == "FAIL"} == set()


def test_k0_uses_profile_terms_only(tmp_path):
    prof = F.test_profile(tmp_path, terms={("prohibited_consequences", "arrest"): ["zzarrestword"],
                                           ("negation_tokens",): ["zznot"],
                                           ("offer_class_keywords", "settlement"): ["zzsettle"],
                                           ("offer_class_keywords", "penalty_waiver"): ["zzwaive"]})
    sp = load_spec(F.SPEC_DIR, profile_path=prof)
    k0 = KeywordFloorK0()
    ctx = EvaluationContext(repetition=1, spec=sp)

    def run(turns):
        return k0.evaluate(F.make_ni(sp, turns), ctx)

    rec = run([("AGENT", "stub zzarrestword stub"), ("BORROWER", "stub")])
    assert rec.gate("G3").status.value == "FAIL" and rec.gate("G3").confidence.value == "HIGH"
    assert rec.gate("G3").critical_status.value == "CONFIRMED"  # lexicon hit without negation (R-11)
    assert run([("AGENT", "stub zznot zzarrestword"), ("BORROWER", "stub")]).gate("G3").status.value == "PASS"
    assert run([("AGENT", "stub"), ("BORROWER", "zzarrestword")]).gate("G3").status.value == "PASS"  # agent only
    assert run([("AGENT", "stub zzsettle"), ("BORROWER", "stub")]).gate("G4").status.value == "FAIL"
    g4 = run([("AGENT", "stub zzwaive"), ("BORROWER", "stub")]).gate("G4")
    assert (g4.status.value, g4.critical_status.value) == ("FAIL", "SUSPECTED")  # authority_unknown
    assert run([("AGENT", "stub seed words settlement waive"), ("BORROWER", "stub")]).verdict.value == "MEETS_BAR"


class StubRuleEngine(RuleEngine):
    def apply(self, extraction, ni, spec):
        from ignosis_eval.contracts.evaluation_record import RecordBody

        return RuleEngineResult(body=RecordBody(gates=[F.gate(g) for g in F.GATES]))

    def integrate(self, result, answers, spec):  # pragma: no cover
        return result


def test_b_interfaces(tmp_path, spec):
    ni = F.make_ni(spec)
    event_type = spec.rubric["extraction_vocabulary"]["borrower_event_types"][0]
    ex = {"events": [{"type": event_type, "turn": 2, "quote": "stub borrower line beta", "role": "BORROWER",
                      "confidence": "MEDIUM"}]}
    assert ExtractionOutput.model_validate(ex).check_vocabulary(spec) == []
    F.write_replay(tmp_path / "replay", "b_extract", ni, [json.dumps(ex)])
    client = ReplayLLMClient(tmp_path / "replay")
    with pytest.raises(NotImplementedError):
        EvaluatorB(client, spec, sleep=NO_SLEEP).evaluate(ni, EvaluationContext(repetition=1, spec=spec))
    rec = EvaluatorB(client, spec, rule_engine=StubRuleEngine(), sleep=NO_SLEEP).evaluate(
        ni, EvaluationContext(repetition=1, spec=spec))
    assert rec.record_status.value == "OK" and rec.gate("G7").status.value == "OUT_OF_SCOPE"
    F.write_replay(tmp_path / "replay2", "b_extract", ni, ['{"events": [{"type": "not_a_vocab_event", "turn": 1, '
                                                            '"quote": "x", "role": "AGENT", "confidence": "LOW"}]}'] * 2)
    rec = EvaluatorB(ReplayLLMClient(tmp_path / "replay2"), spec, rule_engine=StubRuleEngine(),
                     sleep=NO_SLEEP).evaluate(ni, EvaluationContext(repetition=1, spec=spec))
    assert rec.record_status.value == "EVALUATION_FAILED"


def test_system_config_rules():
    SystemConfig(system=System.A, version="x", llm_backend="mock_replay", model_snapshot_id="m-2026-01-01",
                 temperature=0.0, schema_retries=1, transport_retry={})
    for bad in ({"model_snapshot_id": "model-latest"}, {"temperature": 0.2}, {"schema_retries": 2}):
        with pytest.raises(ValueError):
            SystemConfig(**{"system": System.A, "version": "x", "llm_backend": "mock_replay",
                            "model_snapshot_id": "m-1", "temperature": 0.0, "schema_retries": 1,
                            "transport_retry": {}, **bad})
    with pytest.raises(ValueError):
        SystemConfig(system=System.K0, version="x", llm_backend="none", prompt_hashes={"p": "0" * 64})
    with pytest.raises(ValueError):
        SystemConfig(system=System.A_PLUS, version="x", llm_backend="none")
    with pytest.raises(ValueError):
        SystemConfig(system=System.B, version="x", llm_backend="none", consistency_rerun=True)


def test_real_backend_fails_closed():
    with pytest.raises(NotImplementedError):
        AnthropicLLMClient("any-snapshot")


def test_prompt_rubric_section_is_generated(spec):
    from ignosis_eval.evaluators.prompts import prompt_hashes, rubric_section

    text = rubric_section(spec)
    assert "G1" in text and "UND-01" in text
    assert "seed_candidates_unreviewed" not in text  # unreviewed lexicon seeds never reach a prompt
    h = prompt_hashes(("a_holistic",), spec)
    assert set(h) == {"a_holistic", "rubric_section"}
