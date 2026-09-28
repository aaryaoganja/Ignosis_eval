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
    assert rec.confidence_source.value == "SELF_REPORTED" and rec.gate("G3").confidence.value == "HIGH"  # AJ-06
    assert rec.gate("G7").status.value == "OUT_OF_SCOPE"  # front-end merge: pre-checks replace A's own values


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
    ex = {"events": [{"id": "e1", "type": event_type, "turn": 2, "quote": "stub borrower line beta",
                      "role": "BORROWER", "confidence": "MEDIUM", "strength": "explicit"}]}
    assert ExtractionOutput.model_validate(ex).check_vocabulary(spec) == []
    F.write_replay(tmp_path / "replay", "b_extract", ni, [json.dumps(ex)])
    client = ReplayLLMClient(tmp_path / "replay")
    with pytest.raises(NotImplementedError):
        EvaluatorB(client, spec, sleep=NO_SLEEP).evaluate(ni, EvaluationContext(repetition=1, spec=spec))
    rec = EvaluatorB(client, spec, rule_engine=StubRuleEngine(), sleep=NO_SLEEP).evaluate(
        ni, EvaluationContext(repetition=1, spec=spec))
    assert rec.record_status.value == "OK" and rec.gate("G7").status.value == "OUT_OF_SCOPE"
    F.write_replay(tmp_path / "replay2", "b_extract", ni, ['{"events": [{"id": "e1", "type": "not_a_vocab_event", '
                                                            '"turn": 1, "quote": "x", "role": "AGENT", '
                                                            '"confidence": "LOW"}]}'] * 2)
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


# ------------------------------------------------------------------ AJ-06: A (uncorrected baseline) vs A+
def _a_violating_record():
    """A's raw output with deliberate H1 / H6 / repair / confidence violations (synthetic stub quotes)."""
    from ignosis_eval.contracts.enums import RepairStatus

    und = F.finding("UND-01", [2], quote="stub borrower line beta", role="BORROWER", severity="MINOR"
                    ).model_copy(update={"repair_status": RepairStatus.REPAIRED})
    return F.record(
        gates={"G3": F.gate("G3", "FAIL", critical_status="CONFIRMED", confidence="HIGH",
                            evidence=[F.ev(3, "stub agent line gamma")]),
               "G4": F.gate("G4", "FAIL", critical_status="CONFIRMED", confidence="HIGH",
                            evidence=[F.ev(5, "words the agent never said")])},
        findings=[F.finding("PLT-02", [1], severity="MINOR"),            # H6: perception code in TRANSCRIPT
                  F.finding("ACC-01", [1]),                                # H1: always-OUT_OF_SCOPE (external truth)
                  und])                                                    # repair flag on a non-allowlisted code


def test_a_keeps_violations_a_plus_removes_them(tmp_path, spec):
    a, ni, ctx = _a(tmp_path, spec, [F.body_json(_a_violating_record())])
    a_rec = a.evaluate(ni, ctx)
    assert ctx.trace.usage["llm_calls"] == 1
    codes = {f.code for f in a_rec.findings}
    assert {"PLT-02", "ACC-01", "UND-01"} <= codes                         # A is not filtered (can violate H1/H6)
    assert a_rec.confidence_source.value == "SELF_REPORTED"
    assert a_rec.gate("G4").confidence.value == "HIGH" and a_rec.gate("G4").critical_status.value == "CONFIRMED"
    und_a = next(f for f in a_rec.findings if f.code == "UND-01")
    assert (und_a.repair_status.value, und_a.severity.value) == ("REPAIRED", "MINOR")

    calls_before = ctx.trace.usage["llm_calls"]
    rec, log = APlusDeriver(spec).derive(a_rec, a_final_raw_output(ctx.trace.responses), ni, spec)
    assert ctx.trace.usage["llm_calls"] == calls_before                    # A+ makes zero LLM calls
    assert rec.confidence_source.value == "COMPUTED" and rec.system.system == "A+"
    codes = {f.code for f in rec.findings}
    assert "PLT-02" not in codes and "ACC-01" not in codes                  # capability + external-truth filters
    g4 = rec.gate("G4")
    assert g4.evidence_unverified and g4.critical_status.value == "SUSPECTED"  # evidence verifier
    assert rec.gate("G3").confidence.value == "MEDIUM"                     # cap: no lexicon hit, no extraction
    und = next(f for f in rec.findings if f.code == "UND-01")
    assert (und.repair_status.value, und.severity.value) == ("UNREPAIRED", "MAJOR")  # repair allowlist {ACC-05}
    assert rec.verdict.value == "CRITICAL_FAIL"


def test_a_plus_steps_follow_rubric_order(tmp_path, spec):
    a, ni, ctx = _a(tmp_path, spec, [F.body_json(_a_violating_record())])
    a_rec = a.evaluate(ni, ctx)
    _, log = APlusDeriver(spec).derive(a_rec, a_final_raw_output(ctx.trace.responses), ni, spec)
    order = []
    for e in log.entries:
        if not e["step"][0].isdigit():
            continue  # the "derive" provenance entry
        n, name = e["step"].split("-", 1)
        if not order or order[-1][0] != int(n):
            order.append((int(n), name.replace("-", "_")))
    nums = [n for n, _ in order]
    assert nums == sorted(nums), f"A+ steps out of order: {nums}"
    ordered = spec.rubric["architecture_application"]["A_PLUS"]["ordered_steps"]
    for n, name in order:
        if n >= 1:
            assert ordered[n - 1] == name
    assert {n for n, _ in order} >= {1, 2, 3, 4, 5, 6, 7}
    assert spec.rubric["architecture_application"]["A_PLUS"]["llm_calls"] == 0


def test_a_short_circuits_not_evaluable_without_llm_call(tmp_path, spec):
    ni = F.make_ni(spec, (("AGENT", "stub agent line alpha"), ("AGENT", "stub agent line beta")))
    assert ni.frontend.evaluability_status.value == "NOT_EVALUABLE"
    F.write_replay(tmp_path / "replay", "a_evaluate", ni, [F.body_json(_g3_record())])
    ctx = EvaluationContext(repetition=1, spec=spec, trace=TraceSink())
    rec = EvaluatorA(ReplayLLMClient(tmp_path / "replay"), spec, sleep=NO_SLEEP).evaluate(ni, ctx)
    assert ctx.trace.usage.get("llm_calls", 0) == 0
    assert rec.verdict.value == "NOT_EVALUABLE" and rec.confidence_source.value == "SELF_REPORTED"
    assert rec.evaluability.status.value == "NOT_EVALUABLE"


def test_a_merge_applies_precheck_failures(tmp_path, spec):
    ni = F.make_ni(spec, header={"call_start_ts": "2026-09-28T21:00:00+05:30"})  # outside the calling window
    F.write_replay(tmp_path / "replay", "a_evaluate", ni, [F.body_json(F.record())])
    ctx = EvaluationContext(repetition=1, spec=spec, trace=TraceSink())
    rec = EvaluatorA(ReplayLLMClient(tmp_path / "replay"), spec, sleep=NO_SLEEP).evaluate(ni, ctx)
    assert rec.gate("G7").status.value == "FAIL" and rec.verdict.value == "CRITICAL_FAIL"
    assert rec.critical_status.value == "CONFIRMED"
