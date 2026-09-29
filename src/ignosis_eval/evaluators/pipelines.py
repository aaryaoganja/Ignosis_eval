"""Evaluators A, A+ and B — reconciled with frozen-contract §11 and experiment-protocol P-3/P-4.

Applied per rubric.yaml › architecture_application (AJ-06):
A   shared front end -> ONE structured LLM call -> schema validation (1 retry, then EVALUATION_FAILED) -> front-end
    merge only (not-evaluable short-circuit: no LLM call; pre-checks G7/G8/G9/POL-01b). confidence_source
    SELF_REPORTED; no capability / external-truth filter (A must be able to violate H1 and H6).
A+  derived from A's stored raw output of the same rep through the eight ordered deterministic steps of
    engine/finalize.py (the same modules B uses). Zero LLM calls, no prompt of its own, no tuning; COMPUTED.
B   extraction (LLM #1) -> extraction verifier (quotes, responds_to ids) -> rule engine (SpecRuleEngine: every MVP
    gate and code, engine/code_rules.py) -> targeted batched judgments (LLM #2, only if a rule asks for one) ->
    finalize. B's derivation log (verifier drops, rule decisions, judgment answers) is kept in the trace.
Prompts are unoptimized stubs (evaluators/prompts/); the rubric section, the extraction guide and the output
contracts are generated from the spec and the contracts (template 0.4.0).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import ConfidenceSource, RecordStatus, System
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, RecordBody
from ignosis_eval.contracts.run_manifest import SystemConfig, TransportRetryPolicy
from ignosis_eval.engine.extraction import verify_extraction
from ignosis_eval.engine.finalize import DerivationLog, Facts, finalize
from ignosis_eval.engine.frontend_merge import merge_frontend, not_evaluable_short_circuit, short_circuit_record
from ignosis_eval.evaluators.base import EvaluationContext, Evaluator, input_sha256
from ignosis_eval.evaluators.builder import failed_record, parse_ok_record
from ignosis_eval.evaluators.judgement import (
    ExtractionOutput,
    JudgmentOutput,
    RuleEngine,
    SpecRuleEngine,
    judgment_text,
)
from ignosis_eval.evaluators.llm import LLMClient, LLMRequest, OutputSchemaError, RecordingClient, structured_call
from ignosis_eval.evaluators.prompts import (
    A_PROMPTS,
    B_PROMPTS,
    capability_summary,
    extraction_guide,
    output_schema,
    prompt_hashes,
    render,
    render_transcript,
    rubric_section,
)
from ignosis_eval.spec.loader import Spec


class _LLMSystem(Evaluator):
    PROMPTS: tuple[str, ...] = ()

    def __init__(self, client: LLMClient, spec: Spec, *, max_tokens: int | None = None, seed: int | None = None,
                 transport: TransportRetryPolicy | None = None, sleep: Callable[[float], None] | None = None):
        self.client, self.spec = client, spec
        self.max_tokens, self.seed = max_tokens, seed
        self.transport = transport or TransportRetryPolicy()
        self._sleep = sleep

    def config(self) -> SystemConfig:
        return SystemConfig(system=self.system, version=self.version, llm_backend=self.client.backend,  # type: ignore[arg-type]
                            model_snapshot_id=self.client.model_id, temperature=0.0, seed=self.seed,
                            seed_supported=None, max_tokens=self.max_tokens, structured_output=True,
                            prompt_hashes=prompt_hashes(self.PROMPTS, self.spec),
                            rubric_prompt_template_version=__import__("ignosis_eval.versions").versions
                            .PROMPT_TEMPLATE_VERSION,
                            transport_retry=self.transport, schema_retries=1)

    def _recording(self, ctx: EvaluationContext) -> RecordingClient:
        kw = {"sleep": self._sleep} if self._sleep else {}
        return RecordingClient(self.client, self.transport, ctx.trace, **kw)

    def _request(self, task: str, content: str, ni: NormalizedInput, ctx: EvaluationContext) -> LLMRequest:
        sha = input_sha256(ni)
        return LLMRequest(request_id=f"{self.system.value}-{task}-r{ctx.repetition}-{sha[:12]}", task=task,
                          model_id=self.client.model_id, temperature=0.0, max_tokens=self.max_tokens,
                          messages=[{"role": "user", "content": content}],
                          metadata={"input_sha256": sha, "repetition": ctx.repetition, "seed": self.seed})


class EvaluatorA(_LLMSystem):
    system, version = System.A, "0.2.0"
    PROMPTS = A_PROMPTS

    def evaluate(self, ni: NormalizedInput, ctx: EvaluationContext) -> EvaluationRecord:
        spec, info = ctx.spec, self.system_info()
        if not_evaluable_short_circuit(ni):  # front-end merge: no LLM call on a NOT_EVALUABLE call
            return short_circuit_record(ni, spec, info, ConfidenceSource.SELF_REPORTED)
        content = render("a_holistic", profile_id=spec.profile_id, profile_version=spec.profile_version,
                         gates=rubric_section(spec), defects="(see the generated rubric section above)",
                         input_mode=ni.input_mode.value, capabilities=capability_summary(ni, spec),
                         transcript=render_transcript(ni), output_schema=output_schema("a_holistic"))
        req = self._request("a_evaluate", content, ni, ctx)
        rec, _raw, err, attempts = structured_call(self._recording(ctx), req,
                                                   lambda c: parse_ok_record(c, ni, spec, info))
        if rec is None:
            return failed_record(ni, spec, info, err or "schema-invalid", attempts)
        return merge_frontend(rec, ni)  # pre-checks only; nothing else is applied to A


def a_final_raw_output(trace_responses: list[dict]) -> str | None:
    """The raw content of A's last successful response in a rep (what A+ derives from)."""
    ok = [r for r in trace_responses if r.get("ok")]
    return ok[-1]["content"] if ok else None


class APlusDeriver:
    """A+ — no LLM call, no prompt, no tuning. Derived per rep from A's stored raw output (P-4, P-6)."""

    system, version = System.A_PLUS, "0.1.0"

    def __init__(self, spec: Spec):
        self.spec = spec

    def config(self) -> SystemConfig:
        return SystemConfig(system=System.A_PLUS, version=self.version, llm_backend="none", derived_from="A")

    def system_info(self):  # noqa: ANN201
        c = self.config()
        from ignosis_eval.contracts.evaluation_record import SystemInfo

        return SystemInfo(system=c.system.value, version=c.version)

    def derive(self, a_record: EvaluationRecord | None, a_raw_output: str | None, ni: NormalizedInput,
               spec: Spec) -> tuple[EvaluationRecord, DerivationLog]:
        log = DerivationLog()
        info = self.system_info()
        if not_evaluable_short_circuit(ni) and a_record is not None and a_record.record_status is RecordStatus.OK:
            log.add("derive", "record", "A short-circuited on a NOT_EVALUABLE front end; deterministic V2 record")
            return short_circuit_record(ni, spec, info, ConfidenceSource.COMPUTED), log
        if a_record is None or a_record.record_status is RecordStatus.EVALUATION_FAILED or a_raw_output is None:
            log.add("derive", "record", "A produced no valid output in this rep -> A+ EVALUATION_FAILED")
            return failed_record(ni, spec, info, "A output unavailable or EVALUATION_FAILED", 0), log
        body = RecordBody.model_validate(json.loads(a_raw_output))
        log.add("derive", "record", "derived from A raw output", a_verdict=body.verdict.value if body.verdict else None)
        return finalize(body, ni, spec, system=info, facts=Facts(), log=log), log


class EvaluatorB(_LLMSystem):
    system, version = System.B, "0.2.0"
    PROMPTS = B_PROMPTS

    def __init__(self, client: LLMClient, spec: Spec, *, rule_engine: RuleEngine | None = None, **kw):
        super().__init__(client, spec, **kw)
        self.rule_engine = rule_engine or SpecRuleEngine()

    def evaluate(self, ni: NormalizedInput, ctx: EvaluationContext) -> EvaluationRecord:
        spec, info = ctx.spec, self.system_info()
        if not_evaluable_short_circuit(ni):  # shared front end: no LLM call on a NOT_EVALUABLE call
            return short_circuit_record(ni, spec, info, ConfidenceSource.COMPUTED)
        rec_client = self._recording(ctx)
        content = render("b_extraction", rubric_section=rubric_section(spec), input_mode=ni.input_mode.value,
                         transcript=render_transcript(ni), extraction_guide=extraction_guide(spec),
                         output_schema=output_schema("b_extraction"))

        def parse_extraction(c: str) -> ExtractionOutput:
            try:
                ex = ExtractionOutput.model_validate(json.loads(c))
            except (json.JSONDecodeError, ValueError) as exc:
                raise OutputSchemaError(str(exc)) from exc
            errs = ex.check_vocabulary(spec)
            if errs:
                raise OutputSchemaError("; ".join(errs))
            return ex

        ex, _, err, attempts = structured_call(rec_client, self._request("b_extract", content, ni, ctx), parse_extraction)
        if ex is None:
            return failed_record(ni, spec, info, err or "schema-invalid extraction", attempts)
        log = DerivationLog()
        ex = verify_extraction(ex, ni, spec, log)
        result = self.rule_engine.apply(ex, ni, spec)
        if result.judgments_needed:
            jcontent = render("b_judgments", judgments=judgment_text(spec, result.judgments_needed),
                              transcript=render_transcript(ni), output_schema=output_schema("b_judgments"))

            def parse_j(c: str) -> JudgmentOutput:
                try:
                    return JudgmentOutput.model_validate(json.loads(c))
                except (json.JSONDecodeError, ValueError) as exc:
                    raise OutputSchemaError(str(exc)) from exc

            ans, _, err, attempts = structured_call(rec_client, self._request("b_judge", jcontent, ni, ctx), parse_j)
            if ans is None:
                return failed_record(ni, spec, info, err or "schema-invalid judgments", attempts)
            result = self.rule_engine.integrate(result, ans, spec)
        for entry in result.log:
            log.add("rule-engine", str(entry.get("judgment") or entry.get("rule") or entry.get("check") or "call"),
                    "decision", **{k: v for k, v in entry.items() if k not in ("step", "target", "change")})
        record = finalize(result.body, ni, spec, system=info, facts=result.facts, log=log)
        ctx.trace.derivation.extend(log.entries)
        return record
