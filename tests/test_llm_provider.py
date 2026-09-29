"""The OpenAI-compatible provider client (evaluators/llm.py) against a local fake HTTP server; no network, no key."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import factories as F
from ignosis_eval.contracts.run_manifest import TransportRetryPolicy
from ignosis_eval.evaluators.base import EvaluationContext, TraceSink
from ignosis_eval.evaluators.llm import (
    LLMRequest,
    OpenAIChatClient,
    ProviderConfigError,
    ProviderRequestError,
    RecordingClient,
    TransportError,
    backend_family,
)
from ignosis_eval.evaluators.pipelines import EvaluatorA

SNAPSHOT = "test-model-2026-01-01"
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


class _Fake(BaseHTTPRequestHandler):
    replies: list[tuple[int, dict]] = []
    seen: list[dict] = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        type(self).seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        code, payload = type(self).replies.pop(0)
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # noqa: D102
        pass


@pytest.fixture
def server():
    _Fake.replies, _Fake.seen = [], []
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def _ok(content: str, model: str = SNAPSHOT) -> tuple[int, dict]:
    return 200, {"model": model, "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 11, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 3}}}


def _client(url: str) -> OpenAIChatClient:
    return OpenAIChatClient(SNAPSHOT, api_key="test-key-not-real", base_url=url, timeout_s=5, opener=NO_PROXY)


def _req(**meta) -> LLMRequest:
    return LLMRequest("r1", "a_evaluate", SNAPSHOT, 0.0, 256, [{"role": "user", "content": "stub JSON please"}],
                      {"seed": 7, **meta})


def test_config_fails_closed(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderConfigError, match="OPENAI_API_KEY"):
        OpenAIChatClient(SNAPSHOT)
    for bad in (None, "", "some-model", "some-model-latest", "some-model-2026-01-01-latest"):
        with pytest.raises(ProviderConfigError, match="pinned"):
            OpenAIChatClient(bad, api_key="k")
    assert backend_family("openai") == "openai" and backend_family("anthropic") == "anthropic-claude"


def test_payload_and_parse(server):
    _Fake.replies = [_ok('{"a": 1}')]
    resp = _client(server).complete(_req(), 1)
    sent = _Fake.seen[0]
    assert sent["path"] == "/v1/chat/completions" and sent["auth"] == "Bearer test-key-not-real"
    assert sent["body"] == {"model": SNAPSHOT, "messages": [{"role": "user", "content": "stub JSON please"}],
                            "temperature": 0.0, "response_format": {"type": "json_object"},
                            "max_completion_tokens": 256, "seed": 7}
    assert (resp.content, resp.input_tokens, resp.output_tokens, resp.cached_tokens) == ('{"a": 1}', 11, 7, 3)


@pytest.mark.parametrize(("code", "exc"), [(429, TransportError), (503, TransportError), (400, ProviderRequestError),
                                           (401, ProviderRequestError)])
def test_http_errors(server, code, exc):
    _Fake.replies = [(code, {"error": {"message": "stub"}})]
    with pytest.raises(exc):
        _client(server).complete(_req(), 1)


def test_transport_error_is_retried_by_the_recording_client(server):
    _Fake.replies = [(429, {"error": {}}), _ok("{}")]
    trace = TraceSink()
    rc = RecordingClient(_client(server), TransportRetryPolicy(), trace, sleep=lambda s: None)
    assert rc.complete(_req(), 1).content == "{}"
    assert trace.usage["transport_retries"] == 1 and trace.usage["input_tokens"] == 11


def test_evaluator_a_through_the_provider(server):
    sp = F.spec()
    ni = F.make_ni(sp)
    _Fake.replies = [_ok(F.body_json(F.record()))]
    ctx = EvaluationContext(repetition=1, spec=sp, trace=TraceSink())
    a = EvaluatorA(_client(server), sp, sleep=lambda s: None)
    rec = a.evaluate(ni, ctx)
    assert rec.record_status.value == "OK" and rec.confidence_source.value == "SELF_REPORTED"
    cfg = a.config()
    assert (cfg.llm_backend, cfg.model_snapshot_id, cfg.temperature) == ("openai", SNAPSHOT, 0.0)
    prompt = _Fake.seen[0]["body"]["messages"][0]["content"]
    assert '"title":"RecordBody"' in prompt  # the generated output contract reaches the model
