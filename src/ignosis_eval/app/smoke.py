"""Live provider smoke test (`ignosis-eval dev smoke`). One synthetic app demo call (fictional; not a benchmark
item) runs through A, A+ and B with the configured Gemini model. It checks that the provider can be used before
a DEV run:

  1. the request succeeds;
  2. the response parses (A's structured record);
  3. B's extraction output passes the extraction schema;
  4. B's judgment output passes the judgment schema (when the rules asked for judgments);
  5. every evaluation record validates against the contract;
  6. no failure became a verdict (EVALUATION_FAILED carries verdict null).

It measures plumbing, not quality: the verdicts it prints are not results. The key is never printed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ignosis_eval.app.service import load_demo_calls
from ignosis_eval.contracts.enums import UnitMode
from ignosis_eval.contracts.evaluation_record import EvaluationRecord
from ignosis_eval.evaluators import provider_config
from ignosis_eval.evaluators.base import EvaluationContext, TraceSink
from ignosis_eval.evaluators.builder import failed_record
from ignosis_eval.evaluators.llm import GeminiClient, LLMClient, LLMUnavailableError, ProviderRequestError, redact
from ignosis_eval.evaluators.pipelines import APlusDeriver, EvaluatorA, EvaluatorB, a_final_raw_output
from ignosis_eval.pipeline.intake import parse_txt
from ignosis_eval.pipeline.normalize import normalize_supplied
from ignosis_eval.spec.loader import Spec

SMOKE_DEMO = "demo-soft-promise"  # its rules ask for judgments, so both B calls are exercised


def _ok_tasks(trace: TraceSink) -> set[str]:
    by_id = {r["request_id"]: r["task"] for r in trace.requests}
    return {by_id.get(r["request_id"], "?") for r in trace.responses if r.get("ok")}


def run_smoke(spec: Spec, *, client: LLMClient | None = None, sleep: Callable[[float], None] | None = None,
              demo_id: str = SMOKE_DEMO) -> dict[str, Any]:
    """Raises ProviderConfigError (no key, invalid model id) before any call."""
    demo = next(d for d in load_demo_calls() if d["id"] == demo_id)
    ni = normalize_supplied(parse_txt(demo["transcript"]), UnitMode.TRANSCRIPT, spec)
    llm = client or GeminiClient()
    seed = provider_config.SEED if llm.backend == provider_config.PROVIDER else None
    a, b = EvaluatorA(llm, spec, seed=seed, sleep=sleep), EvaluatorB(llm, spec, seed=seed, sleep=sleep)
    traces = {"A": TraceSink(), "B": TraceSink()}
    records: dict[str, EvaluationRecord] = {}
    errors: dict[str, str] = {}
    for name, system in (("A", a), ("B", b)):
        try:
            records[name] = system.evaluate(ni, EvaluationContext(repetition=1, spec=spec, trace=traces[name]))
        except (LLMUnavailableError, ProviderRequestError) as exc:
            errors[name] = redact(str(exc))
            records[name] = failed_record(ni, spec, system.system_info(), f"provider: {errors[name]}", 0)
    records["A+"], _ = APlusDeriver(spec).derive(records["A"], a_final_raw_output(traces["A"].responses), ni, spec)

    ok_a, ok_b = _ok_tasks(traces["A"]), _ok_tasks(traces["B"])
    b_fail = records["B"].failure.reason if records["B"].failure else ""
    judged = any(r["task"] == "b_judge" for r in traces["B"].requests)
    valid = {}
    for name, rec in records.items():
        try:
            EvaluationRecord.model_validate(rec.model_dump(mode="json"))
            valid[name] = True
        except ValueError:
            valid[name] = False
    checks = [
        ("request succeeds", bool(ok_a or ok_b), "; ".join(f"{k}: {v}" for k, v in errors.items()) or
         f"{sum(len(t.responses) for t in traces.values())} responses"),
        ("response parses (A structured record)", records["A"].record_status.value == "OK",
         records["A"].failure.reason[:300] if records["A"].failure else "A record OK"),
        ("extraction schema parses (B call 1)", "b_extract" in ok_b and "extraction" not in b_fail,
         b_fail[:300] if "extraction" in b_fail else "extraction accepted"),
        ("judgment schema parses (B call 2)", (not judged) or ("b_judge" in ok_b and "judgments" not in b_fail),
         "no judgment requested by the rules" if not judged else (b_fail[:300] if "judgments" in b_fail
                                                                   else "judgments accepted")),
        ("evaluation records generated", all(valid.values()) and len(valid) == 3, str(valid)),
        ("no failure became a verdict", all(r.verdict is None for r in records.values()
                                            if r.record_status.value == "EVALUATION_FAILED"),
         "EVALUATION_FAILED records carry verdict null"),
    ]
    served = sorted({str(r.get("model_id")) for t in traces.values() for r in t.responses if r.get("ok")})
    return {
        "provider": llm.backend, "model": llm.model_id, "served_model_versions": served,
        "record_system": {k: {"llm_backend": r.system.llm_backend, "model": r.system.model_snapshot_id}
                          for k, r in records.items()},
        "checks": [{"check": c, "status": "PASS" if ok else "FAIL", "detail": d} for c, ok, d in checks],
        "ok": all(ok for _, ok, _ in checks),
        "records": {k: {"status": r.record_status.value, "verdict": r.verdict.value if r.verdict else None}
                    for k, r in records.items()},
        "usage": {k: dict(t.usage) for k, t in traces.items()},
        "note": "Plumbing check on a synthetic demo call; the verdicts are not results.",
    }


__all__ = ["SMOKE_DEMO", "run_smoke"]
