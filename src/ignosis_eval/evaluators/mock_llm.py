"""Deterministic MOCK LLM backend — lets the A / A+ / B pipelines run end-to-end without a model.

The mock ignores the rendered prompt text and answers each task from the structured payload using the
heuristics in evaluators/heuristics.py. Optional seeded noise (`noise_rate`) drops findings (and clears
their gate) so that consistency metrics can be exercised; the noise is a pure function of
(rep_seed, request_id), so runs are exactly reproducible.

NOT an evaluator. Metrics computed on mock runs measure plumbing only.
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any

from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.evaluation_record import EvidenceItem
from ignosis_eval.contracts.evidence import check_evidence
from ignosis_eval.evaluators.heuristics import DEFECT_GATE, judge
from ignosis_eval.evaluators.llm import LLMClient, LLMRequest, LLMResponse, LLMTransientError


class MockLLMClient(LLMClient):
    backend = "mock"

    def __init__(self, model_id: str = "mock-llm/0", noise_rate: float = 0.0, transient_failures: int = 0):
        if not 0.0 <= noise_rate <= 1.0:
            raise ValueError("noise_rate must be in [0, 1]")
        self.model_id = model_id
        self.noise_rate = noise_rate
        self._transient_left = transient_failures  # simulate retryable errors (tests)

    # --------------------------------------------------------------------------------------------
    def complete(self, request: LLMRequest) -> LLMResponse:
        if self._transient_left > 0:
            self._transient_left -= 1
            raise LLMTransientError("simulated transient failure")
        payload = request.metadata.get("payload") or {}
        inp = CanonicalInput.model_validate(payload["input"])
        rng = random.Random(self._seed(request))
        handler = getattr(self, f"_task_{request.task}", None)
        if handler is None:
            raise ValueError(f"mock backend has no handler for task {request.task!r}")
        result = handler(inp, payload, rng)
        content = json.dumps(result, ensure_ascii=False, sort_keys=True)
        prompt_chars = sum(len(m["content"]) for m in request.messages)
        return LLMResponse(request_id=request.request_id, model_id=self.model_id, content=content,
                           input_tokens=prompt_chars // 4, output_tokens=len(content) // 4, latency_ms=0.0)

    @staticmethod
    def _seed(request: LLMRequest) -> int:
        blob = f"{request.metadata.get('rep_seed', 0)}:{request.request_id}".encode()
        return int(hashlib.sha256(blob).hexdigest()[:16], 16)

    def _noisy(self, j: dict[str, Any], rng: random.Random) -> dict[str, Any]:
        if self.noise_rate <= 0:
            return j
        kept, dropped_gates = [], set()
        for f in j.get("findings", []):
            if rng.random() < self.noise_rate:
                gate = DEFECT_GATE.get(f["defect_id"])
                if gate:
                    dropped_gates.add(gate)
            else:
                kept.append(f)
        still_failing = {DEFECT_GATE.get(f["defect_id"]) for f in kept}
        for g in j.get("gates", []):
            if g["gate_id"] in dropped_gates - still_failing and g["status"] == "fail":
                g["status"] = "pass"
        j["findings"] = kept
        return j

    # --------------------------------------------------------------------------------------- tasks
    def _task_holistic_judge(self, inp, payload, rng):
        return self._noisy(judge(inp), rng)

    def _task_verify_findings(self, inp, payload, rng):
        keep = []
        for i, f in enumerate(payload.get("findings", [])):
            evs = [EvidenceItem(evidence_id=f"x{j}", **{k: v for k, v in e.items() if v is not None})
                   for j, e in enumerate(f.get("evidence", []))]
            if evs and all(check_evidence(e, inp).faithful for e in evs):
                keep.append(i)
        return {"keep": keep}

    def _task_gate_check(self, inp, payload, rng):
        gid = payload["gate_id"]
        j = self._noisy(judge(inp), rng)
        gate = next((g for g in j["gates"] if g["gate_id"] == gid),
                    {"gate_id": gid, "status": "inconclusive", "evidence": []})
        findings = [f for f in j["findings"] if DEFECT_GATE.get(f["defect_id"]) == gid]
        return {"gate": gate, "findings": findings}

    def _task_defect_scan(self, inp, payload, rng):
        j = judge(inp)
        return {"evaluability": j["evaluability"], "dimensions": j["dimensions"], "confidence": j["confidence"],
                "findings": [f for f in j["findings"] if f["defect_id"] not in DEFECT_GATE]}

    def _task_outcome_extract(self, inp, payload, rng):
        j = judge(inp)
        return {"outcome": j["outcome"], "primary_attribution": j["primary_attribution"]}
