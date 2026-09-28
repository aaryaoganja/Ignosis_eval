"""Evaluator A, A+ and B — INTERFACE STUBS with minimal pipelines.

All three accept the same normalized Canonical Input and emit the same Evaluation Record contract. The
pipeline shapes below exist to exercise the infrastructure (one call / call + verification pass /
per-gate decomposition). They are NOT the Stage 4 architecture definitions (docs/gap-analysis.md G12) and
their prompts are unoptimized stubs. Replace the internals in the evaluator-design phase; keep the
interface.
"""

from __future__ import annotations

import json
from typing import Any

from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.capabilities import available_capabilities
from ignosis_eval.contracts.enums import EvaluatorArchitecture, OutcomeCode
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, EvidenceItem
from ignosis_eval.contracts.evidence import check_evidence
from ignosis_eval.contracts.run_manifest import EvaluatorRunConfig, RetryPolicy
from ignosis_eval.evaluators.base import EvaluationContext, Evaluator, config_hash
from ignosis_eval.evaluators.builder import RecordBuilder
from ignosis_eval.evaluators.judgement import EvaluatorOutputError, apply_judgement
from ignosis_eval.evaluators.llm import LLMClient, LLMRequest, RecordingLLMClient
from ignosis_eval.evaluators.prompts import prompt_hashes, render, render_transcript

DEFAULT_RETRY = RetryPolicy(max_attempts=3, backoff_initial_s=1.0, backoff_multiplier=2.0,
                            retry_on=["LLMTransientError"])


class LLMEvaluator(Evaluator):
    PROMPTS: tuple[str, ...] = ()

    def __init__(self, client: LLMClient, *, temperature: float = 0.0, max_output_tokens: int = 2048,
                 retry_policy: RetryPolicy = DEFAULT_RETRY, options: dict[str, Any] | None = None, sleep=None):
        self.client = client
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.retry_policy = retry_policy
        self.options = dict(options or {})
        self._sleep = sleep

    def run_config(self) -> EvaluatorRunConfig:
        ph = prompt_hashes(self.PROMPTS)
        cfg = {"name": self.name, "version": self.version, "architecture": self.architecture.value,
               "backend": self.client.backend, "model_id": self.client.model_id, "temperature": self.temperature,
               "max_output_tokens": self.max_output_tokens, "prompts": ph, "options": self.options,
               "retry": self.retry_policy.model_dump()}
        return EvaluatorRunConfig(
            name=self.name, version=self.version, architecture=self.architecture, llm_backend=self.client.backend,
            model_id=self.client.model_id, temperature=self.temperature, max_output_tokens=self.max_output_tokens,
            prompt_hashes=ph, retry_policy=self.retry_policy, options=self.options, config_hash=config_hash(cfg))

    # ------------------------------------------------------------------------------------ helpers
    def _builder(self, inp: CanonicalInput, ctx: EvaluationContext) -> RecordBuilder:
        return RecordBuilder(self.info(), inp, ctx.profile, ctx.profile_sha256, ctx.rep_seed)

    def _common(self, inp: CanonicalInput, ctx: EvaluationContext) -> dict[str, str]:
        p = ctx.profile
        return {
            "profile_id": p.profile_id, "profile_version": p.profile_version, "input_mode": inp.input_mode.value,
            "capabilities": ", ".join(sorted(c.value for c in available_capabilities(inp))) or "none",
            "gates": "; ".join(f"{g.gate_id}: {g.title}" for g in p.gates),
            "defects": "; ".join(f"{d.defect_id} ({d.severity.value})" for d in p.defects),
            "dimensions": "; ".join(f"{d.dimension_id}: {d.title}" for d in p.dimensions),
            "outcome_codes": ", ".join(o.value for o in OutcomeCode),
            "transcript": render_transcript(inp),
        }

    def _call(self, ctx: EvaluationContext, task: str, prompt: str, payload: dict[str, Any],
              **render_values: object) -> dict[str, Any]:
        client = RecordingLLMClient(self.client, self.retry_policy, ctx.trace,
                                    **({"sleep": self._sleep} if self._sleep else {}))
        n = len(ctx.trace.requests) + 1
        req = LLMRequest(
            request_id=f"r{ctx.repetition}-{n:03d}-{task}", task=task, model_id=self.client.model_id,
            temperature=self.temperature, max_output_tokens=self.max_output_tokens,
            messages=[{"role": "user", "content": render(prompt, **render_values)}],
            metadata={"payload": payload, "rep_seed": ctx.rep_seed, "prompt": prompt})
        resp = client.complete(req)
        try:
            out = json.loads(resp.content)
        except json.JSONDecodeError as exc:
            raise EvaluatorOutputError(f"{task}: model output is not JSON: {exc}") from exc
        if not isinstance(out, dict):
            raise EvaluatorOutputError(f"{task}: model output is not a JSON object")
        return out


class EvaluatorA(LLMEvaluator):
    """Stub: single holistic structured call."""

    name, version, architecture = "evaluator-a", "0.0.1-stub", EvaluatorArchitecture.A
    PROMPTS = ("a_holistic",)

    def _holistic(self, inp: CanonicalInput, ctx: EvaluationContext) -> dict[str, Any]:
        return self._call(ctx, "holistic_judge", "a_holistic", {"input": inp.to_json_dict()},
                          **self._common(inp, ctx))

    def evaluate(self, inp: CanonicalInput, ctx: EvaluationContext) -> EvaluationRecord:
        b = self._builder(inp, ctx)
        conf = apply_judgement(b, self._holistic(inp, ctx))
        return b.build(conf, "self_reported")


class EvaluatorAPlus(EvaluatorA):
    """Stub: A + a verification pass + deterministic evidence grounding (drops ungrounded findings)."""

    name, version, architecture = "evaluator-a-plus", "0.0.1-stub", EvaluatorArchitecture.A_PLUS
    PROMPTS = ("a_holistic", "a_plus_verify")

    def evaluate(self, inp: CanonicalInput, ctx: EvaluationContext) -> EvaluationRecord:
        j = self._holistic(inp, ctx)
        findings = j.get("findings", [])
        common = self._common(inp, ctx)
        v = self._call(ctx, "verify_findings", "a_plus_verify", {"input": inp.to_json_dict(), "findings": findings},
                       transcript=common["transcript"], findings=json.dumps(findings, ensure_ascii=False))
        keep = set(v.get("keep", []))
        grounded = []
        for i, f in enumerate(findings):
            if i not in keep:
                continue
            evs = [EvidenceItem(evidence_id=f"g{k}", **{a: b for a, b in e.items() if b is not None})
                   for k, e in enumerate(f.get("evidence", []))]
            if evs and all(check_evidence(e, inp).faithful for e in evs):
                grounded.append(f)
        j["findings"] = grounded
        b = self._builder(inp, ctx)
        conf = apply_judgement(b, j)
        return b.build(conf, "self_reported")


class EvaluatorB(LLMEvaluator):
    """Stub: decomposed — one call per gate, one defect/evaluability scan, one outcome call; deterministic merge."""

    name, version, architecture = "evaluator-b", "0.0.1-stub", EvaluatorArchitecture.B
    PROMPTS = ("b_gate_check", "b_defect_scan", "b_outcome")

    def evaluate(self, inp: CanonicalInput, ctx: EvaluationContext) -> EvaluationRecord:
        common = self._common(inp, ctx)
        payload = {"input": inp.to_json_dict()}
        scan = self._call(ctx, "defect_scan", "b_defect_scan", payload, **common)
        j: dict[str, Any] = {"evaluability": scan.get("evaluability"), "gates": [], "findings": list(scan.get("findings", [])),
                             "dimensions": scan.get("dimensions", []), "confidence": scan.get("confidence")}
        for g in ctx.profile.gates:
            r = self._call(ctx, "gate_check", "b_gate_check", {**payload, "gate_id": g.gate_id},
                           gate_id=g.gate_id, gate_description=g.description,
                           gate_requires=", ".join(c.value for c in g.requires) or "none", **common)
            if "gate" not in r:
                raise EvaluatorOutputError(f"gate_check {g.gate_id}: missing 'gate'")
            j["gates"].append(r["gate"])
            j["findings"] += r.get("findings", [])
        oc = self._call(ctx, "outcome_extract", "b_outcome", payload, **common)
        j["outcome"] = oc.get("outcome")
        j["primary_attribution"] = oc.get("primary_attribution")
        b = self._builder(inp, ctx)
        conf = apply_judgement(b, j)
        return b.build(conf, "self_reported")
