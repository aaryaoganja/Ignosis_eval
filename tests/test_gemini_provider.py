"""The Gemini evaluator provider (evaluators/provider_config.py, evaluators/llm.py::GeminiClient) against a local
fake generateContent server. No network, no real key: every key below is an obviously fake test string."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

import factories as F
from ignosis_eval.contracts.enums import System
from ignosis_eval.contracts.run_manifest import TransportRetryPolicy
from ignosis_eval.evaluators import provider_config as PC
from ignosis_eval.evaluators.base import EvaluationContext, TraceSink
from ignosis_eval.evaluators.llm import (
    GeminiClient,
    LLMRequest,
    LLMUnavailableError,
    ProviderConfigError,
    ProviderRequestError,
    RecordingClient,
    TransportError,
    backend_family,
    redact,
)
from ignosis_eval.evaluators.pipelines import EvaluatorA
from ignosis_eval.evaluators.registry import build_llm_client, build_systems

FAKE_KEY = "test-gemini-key-not-real-0001"
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
SP = F.spec()


class _Fake(BaseHTTPRequestHandler):
    replies: list[tuple[int, Any]] = []
    seen: list[dict] = []
    auto: bool = False  # answer by the output contract named in the prompt (A / B extraction / B judgments)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        cls = type(self)
        cls.seen.append({"path": self.path, "key": self.headers.get("x-goog-api-key"),
                         "auth": self.headers.get("Authorization"), "body": body})
        code, payload = _auto_reply(body) if cls.auto else cls.replies.pop(0)
        data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # noqa: D102
        pass


def _ok(text: str, *, thought: str | None = None, finish: str = "STOP") -> tuple[int, dict]:
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": text}]
    return 200, {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
                 "usageMetadata": {"promptTokenCount": 21, "candidatesTokenCount": 9, "thoughtsTokenCount": 4,
                                   "cachedContentTokenCount": 2, "totalTokenCount": 34},
                 "modelVersion": "gemini-3.8-flash-001"}


def _auto_reply(body: dict) -> tuple[int, dict]:
    prompt = "".join(p["text"] for c in body["contents"] for p in c["parts"])
    if '"title":"RecordBody"' in prompt:
        return _ok(F.body_json(F.record(system="A")))
    if '"title":"ExtractionOutput"' in prompt:
        return _ok(json.dumps({"events": [], "turn_languages": {}}))
    return _ok(json.dumps({"answers": []}))


@pytest.fixture
def server():
    _Fake.replies, _Fake.seen, _Fake.auto = [], [], False
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1beta"
    srv.shutdown()


@pytest.fixture
def gemini_env(monkeypatch, server):
    """The runtime environment of a deployed evaluator: key and base URL from env only."""
    monkeypatch.setenv(PC.API_KEY_ENV, FAKE_KEY)
    monkeypatch.setenv(PC.BASE_URL_ENV, server)
    monkeypatch.delenv(PC.MODEL_ENV, raising=False)
    for v in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(v, "127.0.0.1,localhost")
    return server


def _client(url: str, **kw) -> GeminiClient:
    return GeminiClient(api_key=FAKE_KEY, base_url=url, timeout_s=5, opener=NO_PROXY, **kw)


def _req(messages=None, **meta) -> LLMRequest:
    return LLMRequest("r1", "a_evaluate", PC.DEFAULT_MODEL, 0.0, 512,
                      messages or [{"role": "user", "content": "stub JSON please"}], {"seed": 0, **meta})


# ------------------------------------------------------------------------------------------ configuration
def test_central_config_defaults_and_overrides():
    s = PC.settings({})
    assert (s.provider, s.family, s.model_id, s.temperature, s.seed, s.schema_retries, s.input_modality) == (
        "gemini", "google-gemini", "gemini-3.8-flash", 0.0, 0, 1, "TRANSCRIPT")
    assert s.base_url == "https://generativelanguage.googleapis.com/v1beta" and s.transport.max_retries == 3
    assert PC.settings({PC.MODEL_ENV: "gemini-3.8-pro"}).model_id == "gemini-3.8-pro"
    assert PC.model_id_problem(PC.DEFAULT_MODEL) is None
    for bad in ("", None, "gemini-flash-latest", "gemini-3.8-flash-preview", "gemini-2.0-flash-exp",
                "gemini-exp-1206", "gpt-5", "claude-x", "gemini-3.8-flash; rm"):
        assert PC.model_id_problem(bad), bad
    assert backend_family("gemini") == "google-gemini" != "anthropic-claude"


def test_missing_key_fails_closed_before_any_call(monkeypatch):
    monkeypatch.delenv(PC.API_KEY_ENV, raising=False)
    assert PC.api_key_configured() is False and PC.describe()["api_key_configured"] is False
    with pytest.raises(ProviderConfigError, match="GEMINI_API_KEY is not set"):
        GeminiClient()
    with pytest.raises(ProviderConfigError, match="GEMINI_API_KEY"):
        build_llm_client("gemini")
    monkeypatch.setenv(PC.API_KEY_ENV, "   ")  # blank is missing
    with pytest.raises(ProviderConfigError):
        GeminiClient()
    monkeypatch.setenv(PC.API_KEY_ENV, FAKE_KEY)
    with pytest.raises(ProviderConfigError, match="alias"):
        GeminiClient("gemini-flash-latest")


def test_describe_never_contains_the_key(monkeypatch):
    monkeypatch.setenv(PC.API_KEY_ENV, FAKE_KEY)
    d = PC.describe()
    assert d["api_key_configured"] is True and d["api_key_env"] == "GEMINI_API_KEY"
    assert FAKE_KEY not in json.dumps(d) and FAKE_KEY not in repr(GeminiClient(opener=NO_PROXY))


# ------------------------------------------------------------------------------------------ request / response
def test_payload_headers_and_parse(server):
    _Fake.replies = [_ok('{"a": 1}', thought="internal reasoning is not content")]
    resp = _client(server).complete(_req(), 1)
    sent = _Fake.seen[0]
    assert sent["path"] == "/v1beta/models/gemini-3.8-flash:generateContent"  # the key is never in the URL
    assert sent["key"] == FAKE_KEY and sent["auth"] is None
    assert sent["body"] == {"contents": [{"role": "user", "parts": [{"text": "stub JSON please"}]}],
                            "generationConfig": {"temperature": 0.0, "candidateCount": 1,
                                                 "responseMimeType": "application/json", "maxOutputTokens": 512,
                                                 "seed": 0}}
    assert resp.content == '{"a": 1}' and resp.model_id == "gemini-3.8-flash-001" and resp.stop_reason == "STOP"
    assert (resp.input_tokens, resp.output_tokens, resp.cached_tokens, resp.reasoning_tokens) == (21, 13, 2, 4)


def test_roles_are_mapped_and_merged():
    contents, system = GeminiClient.contents([
        {"role": "system", "content": "sys"}, {"role": "user", "content": "u1"}, {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "m1"}, {"role": "user", "content": "u3"}])
    assert system == "sys"
    assert contents == [{"role": "user", "parts": [{"text": "u1"}, {"text": "u2"}]},
                        {"role": "model", "parts": [{"text": "m1"}]}, {"role": "user", "parts": [{"text": "u3"}]}]


@pytest.mark.parametrize("reply", [
    (200, {"promptFeedback": {"blockReason": "SAFETY"}}),
    (200, {"candidates": []}),
    (200, {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}),
])
def test_empty_or_blocked_output_is_empty_content(server, reply):
    _Fake.replies = [reply]
    resp = _client(server).complete(_req(), 1)
    assert resp.content == ""


@pytest.mark.parametrize(("code", "exc"), [(408, TransportError), (429, TransportError), (500, TransportError),
                                           (503, TransportError), (400, ProviderRequestError),
                                           (401, ProviderRequestError), (403, ProviderRequestError),
                                           (404, ProviderRequestError)])
def test_http_error_mapping(server, code, exc):
    _Fake.replies = [(code, {"error": {"code": code, "message": "stub", "status": "STUB"}})]
    with pytest.raises(exc) as info:
        _client(server).complete(_req(), 1)
    if isinstance(info.value, ProviderRequestError):
        assert info.value.status == code and info.value.is_config_error is (code in (401, 403, 404))
    if code == 404:  # an unavailable model fails clearly and is never swapped for another
        assert "'gemini-3.8-flash' is unavailable" in str(info.value) and "GEMINI_MODEL" in str(info.value)
        assert [x["path"] for x in _Fake.seen] == ["/v1beta/models/gemini-3.8-flash:generateContent"]


def test_invalid_key_is_a_config_error_and_the_key_is_redacted(server):
    shaped = "AI" + "za" + "FAKE" * 9  # built at runtime: no key-shaped literal in the repository
    echoed = {"error": {"code": 400, "message": f"API key not valid: {FAKE_KEY} {shaped}",
                        "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}}
    _Fake.replies = [(400, echoed)]
    with pytest.raises(ProviderRequestError) as info:
        _client(server).complete(_req(), 1)
    assert info.value.is_config_error and FAKE_KEY not in str(info.value) and shaped not in str(info.value)
    assert redact(f"x {FAKE_KEY} y", (FAKE_KEY,)) == "x [REDACTED] y"


def test_non_json_body_and_timeout(server):
    _Fake.replies = [(200, "<html>proxy error</html>")]
    with pytest.raises(ProviderRequestError, match="not JSON"):
        _client(server).complete(_req(), 1)

    def timeout_opener(*a, **k):
        raise TimeoutError("timed out")

    with pytest.raises(TransportError, match="TimeoutError"):
        GeminiClient(api_key=FAKE_KEY, base_url=server, opener=timeout_opener).complete(_req(), 1)


def test_rate_limit_retried_then_exhausted(server):
    _Fake.replies = [(429, {"error": {}}), _ok("{}")]
    trace = TraceSink()
    rc = RecordingClient(_client(server), TransportRetryPolicy(), trace, sleep=lambda s: None)
    assert rc.complete(_req(), 1).content == "{}"
    assert trace.usage["transport_retries"] == 1 and trace.usage["input_tokens"] == 21
    _Fake.replies = [(503, {"error": {}})] * 4
    with pytest.raises(LLMUnavailableError):
        RecordingClient(_client(server), TransportRetryPolicy(), TraceSink(), sleep=lambda s: None).complete(_req(), 1)


# ------------------------------------------------------------------------------------------ evaluators
def test_schema_failure_is_evaluation_failed_never_pass(server):
    ni = F.make_ni(SP)
    _Fake.replies = [_ok("not json"), (200, {"promptFeedback": {"blockReason": "SAFETY"}})]
    rec = EvaluatorA(_client(server), SP, sleep=lambda s: None).evaluate(ni, EvaluationContext(1, SP, TraceSink()))
    assert rec.record_status.value == "EVALUATION_FAILED" and rec.verdict is None
    assert rec.failure is not None and rec.failure.schema_attempts == 2


def test_a_and_b_through_gemini_with_env_config(gemini_env):
    _Fake.auto = True
    systems = build_systems([System.A, System.A_PLUS, System.B], SP, llm_backend="gemini", sleep=lambda s: None)
    ni = F.make_ni(SP)
    trace_a, trace_b = TraceSink(), TraceSink()
    rec_a = systems[System.A].evaluate(ni, EvaluationContext(1, SP, trace_a))  # type: ignore[attr-defined]
    rec_b = systems[System.B].evaluate(ni, EvaluationContext(1, SP, trace_b))  # type: ignore[attr-defined]
    assert rec_a.record_status.value == "OK" and rec_b.record_status.value == "OK"
    for s in (System.A, System.B):
        cfg = systems[s].config()  # type: ignore[attr-defined]
        assert (cfg.llm_backend, cfg.model_snapshot_id, cfg.temperature, cfg.seed, cfg.schema_retries) == (
            "gemini", "gemini-3.8-flash", 0.0, 0, 1)
    assert all(x["body"]["generationConfig"]["seed"] == 0 for x in _Fake.seen)
    assert all(x["key"] == FAKE_KEY for x in _Fake.seen)
    for rec in (rec_a, rec_b):  # the record states the provider and the exact model
        assert (rec.system.llm_backend, rec.system.model_snapshot_id) == ("gemini", "gemini-3.8-flash")
    blob = json.dumps([trace_a.requests, trace_a.responses, trace_b.requests, trace_b.responses,
                       rec_a.model_dump(mode="json"), rec_b.model_dump(mode="json")])
    assert FAKE_KEY not in blob  # the key never reaches a trace or a record


def test_dev_draft_run_end_to_end_through_gemini(gemini_env, tmp_path):
    from ignosis_eval.runner.dev_drafts import DraftRunConfig, run_dev_drafts

    _Fake.auto = True
    items = ["G-02", "K-01"]
    res = run_dev_drafts(DraftRunConfig(
        drafts_dir=F.REPO / "bench" / "dev" / "transcripts", bench_root=F.REPO / "bench", results_root=tmp_path,
        spec_dir=F.SPEC_DIR, systems=[System.K0, System.A, System.A_PLUS, System.B], llm_backend="gemini",
        item_ids=items))
    assert res.completion["status"] == "completed" and res.completion["n_records_written"] == 8
    m = json.loads((res.run_dir / "draft_run.json").read_text(encoding="utf-8"))
    assert m["llm"]["backend"] == "gemini" and m["llm"]["family"] == "google-gemini"
    assert m["llm"]["model_snapshot_id"] == "gemini-3.8-flash" and m["llm"]["seed"] == 0
    everything = "".join(p.read_text(encoding="utf-8") for p in res.run_dir.rglob("*") if p.is_file())
    assert FAKE_KEY not in everything


def test_provider_config_error_mid_run_stops_the_run_with_failed_records(gemini_env, tmp_path):
    from ignosis_eval.runner.dev_drafts import DraftRunConfig, DraftRunError, run_dev_drafts

    _Fake.replies = [(403, {"error": {"code": 403, "message": "permission denied (stub)"}})]
    with pytest.raises(DraftRunError, match="provider configuration error"):
        run_dev_drafts(DraftRunConfig(
            drafts_dir=F.REPO / "bench" / "dev" / "transcripts", bench_root=F.REPO / "bench", results_root=tmp_path,
            spec_dir=F.SPEC_DIR, systems=[System.B], llm_backend="gemini", item_ids=["K-01"]))
    recs = list(tmp_path.rglob("evaluation_record.json"))
    assert len(recs) == 1
    rec = json.loads(recs[0].read_text(encoding="utf-8"))
    assert rec["record_status"] == "EVALUATION_FAILED" and rec.get("verdict") is None


def test_model_override_is_used_exactly(gemini_env, monkeypatch):
    monkeypatch.setenv(PC.MODEL_ENV, "gemini-9.9-test")
    _Fake.auto = True
    rec = build_systems([System.A], SP, llm_backend="gemini", sleep=lambda s: None)[System.A].evaluate(  # type: ignore[attr-defined]
        F.make_ni(SP), EvaluationContext(1, SP, TraceSink()))
    assert {x["path"] for x in _Fake.seen} == {"/v1beta/models/gemini-9.9-test:generateContent"}
    assert rec.system.model_snapshot_id == "gemini-9.9-test"


def test_live_smoke_check(gemini_env):
    """`ignosis-eval dev smoke` on a fake server that answers with the demo call's scripted output."""
    from ignosis_eval.app.service import load_demo_calls
    from ignosis_eval.app.smoke import SMOKE_DEMO, run_smoke

    script = next(d for d in load_demo_calls() if d["id"] == SMOKE_DEMO)["script"]

    def scripted(body: dict) -> tuple[int, dict]:
        prompt = "".join(p["text"] for c in body["contents"] for p in c["parts"])
        if '"title":"RecordBody"' in prompt:
            return _ok(F.body_json(F.record(system="A")))
        if '"title":"ExtractionOutput"' in prompt:
            return _ok(json.dumps(script["extraction"]))
        return _ok(json.dumps({"answers": script["answers"]}))

    global _auto_reply
    original = _auto_reply
    _auto_reply = scripted
    _Fake.auto = True
    try:
        out = run_smoke(SP, sleep=lambda s: None)
    finally:
        _auto_reply = original
    assert out["ok"] and {c["status"] for c in out["checks"]} == {"PASS"}, out["checks"]
    assert any(c["check"].startswith("judgment") and c["detail"] == "judgments accepted" for c in out["checks"])
    assert out["record_system"]["B"] == {"llm_backend": "gemini", "model": "gemini-3.8-flash"}
    assert out["served_model_versions"] == ["gemini-3.8-flash-001"] and FAKE_KEY not in json.dumps(out)


def test_live_smoke_check_fails_safely(gemini_env, monkeypatch):
    from ignosis_eval.app.smoke import run_smoke

    _Fake.auto = False
    _Fake.replies = [(200, {"promptFeedback": {"blockReason": "SAFETY"}})] * 4
    out = run_smoke(SP, sleep=lambda s: None)
    status = {c["check"]: c["status"] for c in out["checks"]}
    assert out["ok"] is False and status["no failure became a verdict"] == "PASS"
    assert out["records"]["A"] == {"status": "EVALUATION_FAILED", "verdict": None}
    assert out["records"]["B"]["verdict"] is None and out["records"]["A+"]["verdict"] is None
    monkeypatch.delenv(PC.API_KEY_ENV)
    with pytest.raises(ProviderConfigError):
        run_smoke(SP)
