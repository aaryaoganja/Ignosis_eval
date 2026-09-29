"""The review app (src/ignosis_eval/app): API, demo/replay, live path against a local fake Gemini server, failure
handling, uploads, rendering data and the reliability view. No network, no real key: every key below is an
obviously fake test string, and every transcript is a synthetic stub or an app demo call."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import factories as F
from ignosis_eval.app.server import create_app
from ignosis_eval.app.service import EvaluateRequest, ReviewService, load_demo_calls, result_view
from ignosis_eval.evaluators import provider_config as PC

FAKE_KEY = "test-gemini-key-not-real-app-0002"
SP = F.spec()
STUB = "AGENT: stub agent opens zz org\nBORROWER: stub borrower affirms\nAGENT: stub agent closes\n"
DEMOS = {d["id"]: d for d in load_demo_calls()}


class _Gemini(BaseHTTPRequestHandler):
    mode: str = "auto"  # auto | http500 | http403 | not_json | blocked
    seen: list[dict] = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        cls = type(self)
        cls.seen.append({"path": self.path, "key": self.headers.get("x-goog-api-key"), "body": body})
        prompt = "".join(p["text"] for c in body["contents"] for p in c["parts"])
        code, payload = 200, None
        if cls.mode == "http500":
            code, payload = 500, {"error": {"code": 500, "message": "stub internal"}}
        elif cls.mode == "http403":
            code, payload = 403, {"error": {"code": 403, "message": "stub denied"}}
        elif cls.mode == "blocked":
            payload = {"promptFeedback": {"blockReason": "SAFETY"}}
        elif cls.mode == "not_json":
            payload = _cand("this is not json")
        elif '"title":"ExtractionOutput"' in prompt:
            payload = _cand(json.dumps({"events": [], "turn_languages": {}, "call_frame": {"stage_hint": "pre_due"}}))
        else:
            payload = _cand(json.dumps({"answers": []}))
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # noqa: D102
        pass


def _cand(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 10}, "modelVersion": "gemini-3.8-flash"}


@pytest.fixture
def no_key(monkeypatch):
    for v in (PC.API_KEY_ENV, PC.MODEL_ENV, PC.BASE_URL_ENV):
        monkeypatch.delenv(v, raising=False)


@pytest.fixture
def client(no_key):
    return TestClient(create_app(ReviewService(SP, sleep=lambda s: None)))


@pytest.fixture
def live(monkeypatch):
    _Gemini.mode, _Gemini.seen = "auto", []
    srv = HTTPServer(("127.0.0.1", 0), _Gemini)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv(PC.API_KEY_ENV, FAKE_KEY)
    monkeypatch.setenv(PC.BASE_URL_ENV, f"http://127.0.0.1:{srv.server_address[1]}/v1beta")
    monkeypatch.delenv(PC.MODEL_ENV, raising=False)
    for v in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(v, "127.0.0.1,localhost")
    yield TestClient(create_app(ReviewService(SP, sleep=lambda s: None)))
    srv.shutdown()


def _eval(c: TestClient, **form: Any):
    files = form.pop("files", None)
    return c.post("/api/evaluate", data={k: v for k, v in form.items() if v is not None}, files=files)


# ------------------------------------------------------------------------------------------ basics
def test_health_config_and_static(client):
    assert client.get("/api/health").json() == {"status": "ok", "version": "0.4.0", "live_evaluation": False}
    cfg = client.get("/api/config").json()
    assert cfg["live_available"] is False and cfg["evaluator"]["api_key_configured"] is False
    assert cfg["evaluator"]["provider"] == "gemini" and cfg["evaluator"]["model_id"] == "gemini-3.8-flash"
    assert [m["label"] for m in cfg["modes"]] == ["Transcript", "Audio + Transcript", "Audio only"]
    assert [m["supported"] for m in cfg["modes"]] == [True, True, False]
    assert cfg["product"]["tagline"].startswith("Ignosis already listens to every call.")
    assert cfg["profile"]["read_only"] is True and cfg["profile"]["profile_version"] == "1.1.1"
    page = client.get("/")
    assert page.status_code == 200 and "Ignosis" in page.text and "/static/app.js" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/calls/ev-nope").status_code == 404


def test_demo_library_is_replayed_through_the_real_engine(client):
    rows = {r["name"]: r for r in client.get("/api/calls").json()}
    assert len(rows) == 5 and all(r["source"] == "DEMO_REPLAY" for r in rows.values())
    by_demo = {d: rows[DEMOS[d]["title"]] for d in DEMOS}
    assert by_demo["demo-settlement"]["verdict_code"] == "CRITICAL_FAIL"
    assert by_demo["demo-settlement"]["dangerous_win"] == "CRITICAL"
    assert by_demo["demo-settlement"]["primary_finding"].startswith("G4")
    assert by_demo["demo-soft-promise"]["verdict_code"] == "NEEDS_ATTENTION"
    assert by_demo["demo-soft-promise"]["primary_finding"].startswith("COM-02")
    assert by_demo["demo-clean-ptp"]["verdict_code"] == "MEETS_BAR"
    assert by_demo["demo-fraud-dispute"]["verdict_code"] == "MEETS_BAR" and by_demo["demo-fraud-dispute"]["clean_loss"]
    assert by_demo["demo-voicemail"]["verdict_code"] == "NOT_EVALUABLE"
    full = client.get(f"/api/calls/{by_demo['demo-settlement']['evaluation_id']}").json()
    assert full["verdict"]["display"] == "Critical Fail — within available evidence"
    assert full["verdict"]["critical_status"] == "CONFIRMED" and full["routing"]["tier"] == 1
    g4 = full["findings"][0]
    assert g4["code"] == "G4" and g4["is_gate"] and g4["attribution"]["label"] == "Agent behaviour"
    assert g4["evidence"][0]["turn"] == 7 and any(t["cited"] for t in full["transcript"])
    assert [e["turn"] for e in full["unverified_commitments"]] == [9]
    assert full["evaluator"]["model"] == "demo-replay/scripted-1" and full["record"]["system"]["system"] == "B"
    assert {c["code"] for c in full["uncertainty"]["inconclusive"]} == {"RES-11"}  # pending threshold: never PASS


def test_demo_via_api_with_browser_line_endings(client):
    d = DEMOS["demo-soft-promise"]
    r = _eval(client, mode="transcript", transcript=d["transcript"].replace("\n", "\r\n"), demo_id=d["id"],
              replay="true").json()
    assert r["source"] == "DEMO_REPLAY" and r["verdict"]["code"] == "NEEDS_ATTENTION"
    edited = d["transcript"].replace("Theek hai.", "Theek hai, pakka.")
    r = _eval(client, mode="transcript", transcript=edited, demo_id=d["id"], replay="true").json()
    assert r["source"] == "UNAVAILABLE" and r["verdict"] is None  # an edited demo is not replayed


# ------------------------------------------------------------------------------------------ no key: never a PASS
def test_custom_transcript_without_key_gives_no_verdict(client):
    r = _eval(client, mode="transcript", transcript=STUB, call_name="Stub call").json()
    assert (r["source"], r["status"], r["verdict"]) == ("UNAVAILABLE", "NOT_RUN", None)
    assert "GEMINI_API_KEY" in r["key_finding"]["explanation"] and "not a pass" in r["action"]
    assert r["frontend"]["evaluability"] == "EVALUABLE" and r["call"]["name"] == "Stub call"


def test_front_end_short_circuit_is_live_without_a_model_call(client):
    r = _eval(client, mode="transcript", transcript="stub line without any role label").json()
    assert r["source"] == "LIVE" and r["verdict"]["code"] == "NOT_EVALUABLE"
    assert r["evaluator"]["model_called"] is False and r["usage"]["llm_calls"] == 0
    assert "ROLE_UNCERTAIN" in r["uncertainty"]["reason_codes"]


# ------------------------------------------------------------------------------------------ modes and uploads
def test_audio_mode_gives_a_pathway_not_a_result(client):
    r = _eval(client, mode="audio", files={"audio_file": ("call.wav", b"RIFF-stub", "audio/wav")})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "ASR_PENDING" and "requires speech recognition" in err["message"]
    assert any("Audio + Transcript" in p for p in err["pathway"])


def test_audio_plus_transcript(client):
    assert _eval(client, mode="audio_transcript", transcript=STUB).json()["error"]["code"] == "AUDIO_MISSING"
    bad = _eval(client, mode="audio_transcript", transcript=STUB, files={"audio_file": ("x.txt", b"x", "text/plain")})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "AUDIO_FORMAT"
    r = _eval(client, mode="audio_transcript", transcript=STUB,
              files={"audio_file": ("call.wav", b"RIFF-synthetic-stub-bytes", "audio/wav")}).json()
    assert r["call"]["unit_mode"] == "A+T" and r["call"]["has_audio"] is True and r["verdict"] is None


def test_transcript_uploads_and_validation(client):
    ok = _eval(client, mode="transcript", files={"transcript_file": ("call.txt", STUB.encode(), "text/plain")})
    assert ok.status_code == 200 and ok.json()["call"]["n_turns"] == 3
    js = json.dumps({"turns": [{"speaker": "AGENT", "text": "stub a"}, {"speaker": "BORROWER", "text": "stub b"}]})
    assert _eval(client, mode="transcript", files={"transcript_file": ("c.json", js.encode(), "application/json")}
                 ).json()["call"]["n_turns"] == 2
    cases = [({"files": {"transcript_file": ("c.pdf", b"%PDF", "application/pdf")}}, 400, "TRANSCRIPT_FORMAT"),
             ({"transcript": "{not json"}, 400, "TRANSCRIPT_INVALID"),
             ({"transcript": "   "}, 400, "TRANSCRIPT_MISSING"),
             ({"transcript": "AGENT: " + "x" * 300_000}, 413, "TRANSCRIPT_TOO_LARGE"),
             ({"transcript": "AGENT: x\n" * 500}, 413, "TRANSCRIPT_TOO_LONG")]
    for form, status, code in cases:
        r = _eval(client, mode="transcript", **form)
        assert (r.status_code, r.json()["error"]["code"]) == (status, code), form
    assert _eval(client, mode="video", transcript=STUB).json()["error"]["code"] == "MODE_INVALID"


def test_reliability_view(client, tmp_path):
    r = client.get("/api/reliability").json()
    assert r["label"] == "DEV ENGINEERING MEASUREMENT - NOT FINAL RELIABILITY EVIDENCE"
    assert r["final_validation"] == "FINAL RELIABILITY VALIDATION: PENDING"
    assert r["holdout"] == "not started" and r["red_team"] == "not started" and r["status"] == "OK"
    assert r["benchmark"]["human_review_pending"] is True and r["systems"]["K0"]["status"] == "EXECUTED"
    for s in ("A", "A+", "B"):  # the committed baseline predates a key: never shown as executed
        if r["systems"][s]["status"] != "EXECUTED":
            assert r["systems"][s]["status"] == "NOT EXECUTED"
    svc = ReviewService(SP, report_path=tmp_path / "missing.json")
    assert svc.reliability()["status"] == "NO_DEV_REPORT"


# ------------------------------------------------------------------------------------------ live (fake Gemini)
def test_live_transcript_evaluation_end_to_end(live, caplog):
    caplog.set_level(logging.DEBUG)
    assert live.get("/api/config").json()["live_available"] is True
    resp = _eval(live, mode="transcript", transcript=STUB)
    r = resp.json()
    assert r["source"] == "LIVE" and r["status"] == "OK" and r["verdict"]["code"] in (
        "MEETS_BAR", "NEEDS_ATTENTION", "CRITICAL_FAIL")
    assert r["evaluator"]["model_called"] is True and r["evaluator"]["model"] == "gemini-3.8-flash"
    assert r["evaluator"]["provider"] == "gemini" and r["evaluator"]["served_model_versions"] == ["gemini-3.8-flash"]
    assert (r["record"]["system"]["llm_backend"], r["record"]["system"]["model_snapshot_id"]) == (
        "gemini", "gemini-3.8-flash")
    assert r["usage"]["llm_calls"] >= 1 and r["usage"]["input_tokens"] >= 30
    assert _Gemini.seen and all(x["key"] == FAKE_KEY for x in _Gemini.seen)
    assert all(x["body"]["generationConfig"]["temperature"] == 0.0 for x in _Gemini.seen)
    everything = resp.text + live.get("/api/config").text + live.get("/api/calls").text + caplog.text
    assert FAKE_KEY not in everything  # the key never reaches the browser or the logs


def test_live_demo_runs_live_unless_replay_is_asked(live):
    d = DEMOS["demo-clean-ptp"]
    assert _eval(live, mode="transcript", transcript=d["transcript"], demo_id=d["id"]).json()["source"] == "LIVE"
    assert _eval(live, mode="transcript", transcript=d["transcript"], demo_id=d["id"], replay="true"
                 ).json()["source"] == "DEMO_REPLAY"


@pytest.mark.parametrize("mode", ["http500", "http403", "not_json", "blocked"])
def test_provider_failures_are_evaluation_failed_never_a_verdict(live, mode, caplog):
    _Gemini.mode = mode
    r = _eval(live, mode="transcript", transcript=STUB).json()
    assert r["status"] == "EVALUATION_FAILED" and r["verdict"]["code"] is None
    assert r["record"]["record_status"] == "EVALUATION_FAILED" and r["record"]["verdict"] is None
    assert r["action"].endswith("A failed evaluation is never a pass.")
    assert FAKE_KEY not in json.dumps(r) + caplog.text


def test_invalid_model_override_is_unavailable_not_a_call(live, monkeypatch):
    monkeypatch.setenv(PC.MODEL_ENV, "gemini-flash-latest")
    _Gemini.seen = []
    r = _eval(live, mode="transcript", transcript=STUB).json()
    assert r["source"] == "UNAVAILABLE" and r["verdict"] is None and _Gemini.seen == []


# ------------------------------------------------------------------------------------------ rendering data
def test_result_view_orders_gates_first_and_keeps_failures_explicit():
    svc = ReviewService(SP)
    v = svc.evaluate(EvaluateRequest(mode="transcript", transcript_text=DEMOS["demo-settlement"]["transcript"],
                                     demo_id="demo-settlement", replay=True))
    assert [f["code"] for f in v["findings"]][:1] == ["G4"]
    assert [f["severity"] for f in v["findings"]] == sorted(
        (f["severity"] for f in v["findings"]), key=["CRITICAL", "MAJOR", "MINOR", "INFORMATIONAL"].index)
    ni = F.make_ni(SP)
    failed = result_view(F.failed(), ni, SP, source="LIVE", call={"name": "x"}, trace=None, latency_s=0.0)
    assert failed["status"] == "EVALUATION_FAILED" and failed["verdict"]["code"] is None


def test_static_client_never_handles_provider_secrets():
    static = Path(__file__).resolve().parents[1] / "src" / "ignosis_eval" / "app" / "static"
    text = "".join(p.read_text(encoding="utf-8") for p in static.iterdir() if p.is_file())
    assert "generativelanguage" not in text and "x-goog-api-key" not in text and "AIza" not in text
    assert "innerHTML" not in text  # dynamic text goes through textContent only


def test_server_listens_on_railway_port(monkeypatch):
    import uvicorn

    from ignosis_eval.app.__main__ import main

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.setenv("PORT", "4321")
    monkeypatch.delenv("HOST", raising=False)
    main()
    assert (seen["host"], seen["port"], seen["factory"]) == ("0.0.0.0", 4321, True)
    assert seen["app"] == "ignosis_eval.app.server:create_app"
    monkeypatch.delenv("PORT")
    main()
    assert seen["port"] == 8000
    docker = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["python", "-m", "ignosis_eval.app"]' in docker


def test_sample_recording_is_fictional_synthetic_and_linked(client):
    """The sample recording: a synthetic rendering of a demo call's transcript, linked to results that use it."""
    import hashlib
    import wave

    demos = {d["id"]: d for d in client.get("/api/demo-calls").json()}
    sample = [d for d in demos.values() if d.get("audio")]
    assert len(sample) == 1 and sample[0]["id"] == "demo-settlement" and sample[0]["illustrates"]
    meta = DEMOS["demo-settlement"]["audio"]
    assert meta["fictional"] is True and "espeak-ng" in meta["generator"]
    data = client.get(sample[0]["audio"]["url"]).content
    assert hashlib.sha256(data).hexdigest() == meta["sha256"] and len(data) < 1_500_000
    path = Path(__file__).resolve().parents[1] / "src" / "ignosis_eval" / "app" / "static" / meta["file"]
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getframerate()) == (1, 8000) and 20 < w.getnframes() / 8000 < 90
    # Audio + Transcript with the sample: replayed demo evaluation, with the recording linked
    r = _eval(client, mode="audio_transcript", transcript=sample[0]["transcript"], demo_id="demo-settlement",
              replay="true", files={"audio_file": ("demo-settlement.wav", data, "audio/wav")}).json()
    assert r["source"] == "DEMO_REPLAY" and r["call"]["unit_mode"] == "A+T" and r["verdict"]["code"] == "CRITICAL_FAIL"
    assert r["call"]["audio_url"] == sample[0]["audio"]["url"]
    # Audio only with the sample: the known limitation, with the working alternative, never a result
    a = _eval(client, mode="audio", files={"audio_file": ("demo-settlement.wav", data, "audio/wav")})
    assert a.status_code == 422 and a.json()["error"]["code"] == "ASR_PENDING"
    # without a key, the sample's transcript in Audio + Transcript without replay gets no verdict
    n = _eval(client, mode="audio_transcript", transcript=sample[0]["transcript"],
              files={"audio_file": ("demo-settlement.wav", data, "audio/wav")}).json()
    assert n["source"] == "UNAVAILABLE" and n["verdict"] is None and n["call"]["audio_url"]
