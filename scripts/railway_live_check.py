"""Live check of a deployed review app (e.g. on Railway), end to end through the real Gemini path.

    python scripts/railway_live_check.py https://<your-service>.up.railway.app

The Gemini key stays on the server: this script calls only the app's public API and never sees or sends a key.
It sends one fictional demo call for a LIVE evaluation. The server calls Gemini for B's extraction and
judgments, then runs the rule engine and builds the evaluation record. The script checks that:
  - the request succeeded and the response parsed;
  - a validated record was created;
  - the provider and the model are recorded;
  - a failure is never a verdict;
  - live and DEMO / REPLAY results are labelled apart.
It measures plumbing on a synthetic call; its verdict is not a result.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any

DEMO_ID = "demo-soft-promise"  # its rules ask for judgments, so both of B's model calls are exercised


def _get(base: str, path: str) -> Any:
    with urllib.request.urlopen(base + path, timeout=60) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if path.startswith("/api/") else body


def _post_form(base: str, fields: dict[str, str]) -> tuple[int, Any]:
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items()]
    data = ("".join(parts) + f"--{boundary}--\r\n").encode("utf-8")
    req = urllib.request.Request(base + "/api/evaluate", data=data, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    base = argv[1].rstrip("/")
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    health = _get(base, "/api/health")
    check("health endpoint /api/health", health.get("status") == "ok", json.dumps(health))
    cfg = _get(base, "/api/config")
    ev = cfg["evaluator"]
    check("live evaluation configured (key present, valid model)", cfg["live_available"],
          f"provider={ev['provider']} model={ev['model_id']} ({ev['model_source']}); key configured: "
          f"{ev['api_key_configured']}; model id valid: {ev['model_id_valid']}")
    check("config exposes key presence only, never a key",
          {k for k in ev if k.startswith("api_key")} <= {"api_key_env", "api_key_configured"}
          and isinstance(ev["api_key_configured"], bool), "")
    demo = next(d for d in _get(base, "/api/demo-calls") if d["id"] == DEMO_ID)

    status, live = _post_form(base, {"mode": "transcript", "transcript": demo["transcript"], "demo_id": DEMO_ID,
                                     "replay": "false", "call_name": "live check (fictional demo call)"})
    check("POST /api/evaluate succeeded", status == 200, f"HTTP {status}")
    if status == 200:
        rec = live.get("record") or {}
        system = rec.get("system") or {}
        is_live = live["source"] == "LIVE"  # without a usable key the server replays demos instead (labelled)
        check("labelled LIVE EVALUATION", is_live, live["source_label"])
        check("model was called (request -> response)", is_live and live["evaluator"].get("model_called") is True,
              f"calls={live['usage'].get('llm_calls')} in={live['usage'].get('input_tokens')} "
              f"out={live['usage'].get('output_tokens')} latency={live['usage'].get('latency_s')}s")
        check("response parsed; extraction + judgments accepted; record created",
              is_live and live["status"] == "OK" and rec.get("record_status") == "OK",
              (live.get("key_finding") or {}).get("explanation", "")[:300] if live["status"] != "OK" else
              f"verdict={live['verdict']['code']}")
        check("provider and model recorded in the record",
              system.get("llm_backend") == "gemini" and system.get("model_snapshot_id") == ev["model_id"],
              f"llm_backend={system.get('llm_backend')} model={system.get('model_snapshot_id')} served="
              f"{live['evaluator'].get('served_model_versions')}")
        check("a failure is never a verdict",
              live["status"] != "EVALUATION_FAILED" or (live["verdict"] or {}).get("code") is None, live["status"])
    status, replay = _post_form(base, {"mode": "transcript", "transcript": demo["transcript"], "demo_id": DEMO_ID,
                                       "replay": "true"})
    check("DEMO / REPLAY still works and is labelled apart", status == 200 and replay["source"] == "DEMO_REPLAY",
          replay.get("source_label", ""))
    check("app page served", "Ignosis" in _get(base, "/"), "")

    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    print("Plumbing check on a fictional demo call; the verdict is not a result.")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
