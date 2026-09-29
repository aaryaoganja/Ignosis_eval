"""The review app's evaluation service: intake -> shared front end -> Evaluator B -> result view. No web framework
here, so every rule below is testable without a server.

Sources of a result, always labelled:
- LIVE EVALUATION: B with the Gemini provider (key from the runtime env GEMINI_API_KEY, server side only), or the
  deterministic front end alone when it short-circuits a NOT_EVALUABLE call (no model call is needed then).
- DEMO / REPLAY: a synthetic demo call whose scripted model output (demo_calls.json) is replayed through the real B
  rule engine and finalize. Not a live model result, not a measurement.
- EVALUATOR UNAVAILABLE: no key on the server. The front end still runs; no verdict is produced (never a PASS).

Failures (schema-invalid output twice, empty or blocked output, transport failure after retries, provider errors)
become EVALUATION_FAILED records with a stated reason, never a verdict. Systems run inside ProtectedPathGuard and
see only the normalized input under a random unit alias (P-17). The app reads no gold, case card, design intent or
run tree; the reliability view reads the committed DEV baseline report only.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ignosis_eval.benchmark.layout import BenchLayout
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import ConfidenceSource, UnitMode
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, Evidence, SystemInfo
from ignosis_eval.engine.frontend_merge import not_evaluable_short_circuit, short_circuit_record
from ignosis_eval.evaluators import provider_config
from ignosis_eval.evaluators.base import EvaluationContext, TraceSink
from ignosis_eval.evaluators.builder import failed_record
from ignosis_eval.evaluators.llm import (
    GeminiClient,
    LLMClient,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    ProviderConfigError,
    ProviderRequestError,
    redact,
)
from ignosis_eval.evaluators.pipelines import EvaluatorB
from ignosis_eval.integrity.guard import ProtectedPathGuard
from ignosis_eval.pipeline.intake import ParsedTranscript, TranscriptParseError, parse_json, parse_txt
from ignosis_eval.pipeline.normalize import NormalizationError, audio_ref_from_bytes, normalize_supplied
from ignosis_eval.spec.loader import Spec, repo_root
from ignosis_eval.spec.pending import find_pending
from ignosis_eval.versions import ENGINE_VERSION, FRONTEND_VERSION, PACKAGE_VERSION, PROMPT_TEMPLATE_VERSION

log = logging.getLogger("ignosis_eval.app")

MAX_TRANSCRIPT_BYTES = 256 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_TURNS = 400
LIBRARY_LIMIT = 200
AUDIO_FORMATS = ("wav", "mp3", "m4a")
DEMO_FILE = Path(__file__).with_name("demo_calls.json")
REPORT_ENV = "IGNOSIS_DEV_REPORT"
DEMO_MODEL_ID = "demo-replay/scripted-1"

LIVE, REPLAY, UNAVAILABLE = "LIVE", "DEMO_REPLAY", "UNAVAILABLE"
SOURCE_LABELS = {LIVE: "LIVE EVALUATION", REPLAY: "DEMO / REPLAY", UNAVAILABLE: "EVALUATOR UNAVAILABLE"}
VERDICT_LABELS = {"CRITICAL_FAIL": "Critical Fail", "NEEDS_ATTENTION": "Needs Attention", "MEETS_BAR": "Meets Bar",
                  "NOT_EVALUABLE": "Not Evaluable"}
ATTRIBUTION_LABELS = {"AGENT_BEHAVIOR": "Agent behaviour", "PERCEPTION": "Perception (what the agent heard)",
                      "PLATFORM_AUDIO": "Platform / audio", "CUSTOMER_DRIVEN": "Customer-driven",
                      "INDETERMINATE": "Indeterminate"}
ROUTING_LABELS = {1: "Tier 1 - immediate review: confirmed critical failure",
                  2: "Tier 2 - priority review: suspected critical failure",
                  3: "Tier 3 - review: material Dangerous Win",
                  4: "Tier 4 - remediate: agent behaviour to correct",
                  5: "Tier 5 - fix: minor fixes only",
                  6: "Tier 6 - audit sample: meets bar"}
REASON_LABELS = {
    "ROLE_UNCERTAIN": "speaker roles could not be established", "TRANSCRIPT_UNRELIABLE": "transcript unreliable",
    "AUDIO_POOR": "audio quality too poor", "SPAN_UNRELIABLE": "the relevant span is unreliable",
    "NON_CONVERSATIONAL": "no conversation took place (voicemail, IVR or silence)",
    "CONTRADICTORY_EVIDENCE": "contradictory evidence", "POLICY_UNKNOWN": "the policy is not defined in the profile",
    "EXTERNAL_DATA_REQUIRED": "needs data outside the call (ledger, CRM, knowledge base)",
    "LANGUAGE_UNSUPPORTED": "language not supported", "TRANSCRIPT_TRUNCATED": "the transcript starts mid-call",
    "STAGE_UNKNOWN": "the collection stage is unknown",
    "NO_AGENT_TURN_AFTER_REQUEST": "the call ended before the agent could respond",
    "NO_AGENT_TURN_AFTER_TRIGGER": "the call ended before the agent could respond"}
OOS_LABELS = {"EXTERNAL_DATA_REQUIRED": "needs data outside the call (ledger, CRM, knowledge base, call metadata)",
              "MODE_CAPABILITY": "not observable in this input mode"}
SEVERITY_RANK = {"CRITICAL": 0, "MAJOR": 1, "MINOR": 2, "INFORMATIONAL": 3}


class Mode(str, Enum):
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    AUDIO_TRANSCRIPT = "audio_transcript"


MODE_INFO = {
    Mode.AUDIO: {"label": "Audio", "unit_mode": "A", "supported": False,
                 "note": "Audio-only evaluation needs speech recognition and diarization, which are pending (B-06)."},
    Mode.TRANSCRIPT: {"label": "Transcript", "unit_mode": "TRANSCRIPT", "supported": True,
                      "note": "Evaluates a labelled transcript (AGENT: / BORROWER: lines, or JSON)."},
    Mode.AUDIO_TRANSCRIPT: {"label": "Audio + Transcript", "unit_mode": "A+T", "supported": True,
                            "note": "Evaluates the supplied transcript; the audio is fingerprinted and recorded. "
                                    "Audio-only signals (overlap, audio quality) are reported, not guessed."},
}


class AppError(Exception):
    """A request the app cannot serve, with a message for the reviewer (and a pathway when there is one)."""

    def __init__(self, status: int, code: str, message: str, pathway: list[str] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.pathway = status, code, message, pathway or []

    def to_json(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "pathway": self.pathway}}


@dataclass
class EvaluateRequest:
    mode: str
    transcript_text: str | None = None
    transcript_filename: str | None = None
    transcript_bytes: bytes | None = None
    audio_bytes: bytes | None = None
    audio_filename: str | None = None
    demo_id: str | None = None
    replay: bool = False
    call_name: str | None = None


class ScriptedDemoClient(LLMClient):
    """Replays a demo call's scripted model output (DEMO / REPLAY). It never generates content."""

    backend = "mock_replay"
    model_id = DEMO_MODEL_ID

    def __init__(self, script: dict[str, Any]):
        self.script = script

    def complete(self, request: LLMRequest, attempt: int) -> LLMResponse:
        if request.task == "b_extract":
            content = json.dumps(self.script["extraction"])
        elif request.task == "b_judge":
            content = json.dumps({"answers": self.script.get("answers", [])})
        else:
            content = ""  # no script for this task: schema retry, then EVALUATION_FAILED
        return LLMResponse(request.request_id, self.model_id, content)


def load_demo_calls(path: Path = DEMO_FILE) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8"))["calls"])


def catalog(spec: Spec) -> dict[str, dict[str, Any]]:
    """Check id -> name, dimension, severity, description (rubric gates, codes, platform signals, out of scope)."""
    r = spec.rubric
    dims = r.get("dimensions", {})
    out: dict[str, dict[str, Any]] = {}
    for group in ("gates", "codes", "platform_signals", "out_of_scope_codes"):
        for c in r.get(group) or []:
            d = c.get("dimension") or ("PLATFORM" if group == "platform_signals" else None)
            out[c["id"]] = {"name": c.get("name", c["id"]), "dimension": d,
                            "dimension_name": (dims.get(d) or {}).get("name") if d else None,
                            "severity": c.get("severity") or c.get("severity_default"),
                            "description": " ".join(str(c.get("description") or c.get("rule") or "").split())}
    return out


def _same_text(a: str, b: str) -> bool:
    """Equal up to line endings (browsers send textarea newlines as CRLF) and surrounding whitespace."""
    def norm(x: str) -> str:
        return "\n".join(line.rstrip() for line in x.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n"))
    return norm(a) == norm(b)


def _reason_text(codes: list[str]) -> str:
    return "; ".join(REASON_LABELS.get(c, c.replace("_", " ").lower()) for c in codes)


def _ev(e: Evidence) -> dict[str, Any]:
    if e.header_field:
        return {"turn": None, "role": None, "quote": None, "header_field": e.header_field}
    return {"turn": e.turn, "role": e.role.value if e.role else None, "quote": e.quote, "element": e.element}


# ------------------------------------------------------------------------------------------ result view
def result_view(record: EvaluationRecord | None, ni: NormalizedInput, spec: Spec, *, source: str, call: dict,
                trace: TraceSink | None, latency_s: float, unavailable_reason: str | None = None) -> dict[str, Any]:
    cat = catalog(spec)
    suffix = spec.rubric.get("verdict_display_label_suffix", "")
    findings: list[dict[str, Any]] = []
    inconclusive: list[dict[str, Any]] = []
    out_of_scope: list[dict[str, Any]] = []
    cited: set[int] = set()
    view: dict[str, Any] = {
        "source": source, "source_label": SOURCE_LABELS[source], "call": call,
        "evaluator": _evaluator_info(record, source, bool(trace and trace.usage["llm_calls"])),
        "frontend": {"evaluability": ni.frontend.evaluability_status.value,
                     "reason_codes": [r.value for r in ni.frontend.reason_codes],
                     "reason_text": _reason_text([r.value for r in ni.frontend.reason_codes]),
                     "prechecks": {g.gate.value: g.status.value for g in ni.frontend.prechecks}},
        "usage": {**(trace.usage if trace else TraceSink().usage), "latency_s": round(latency_s, 3)},
    }
    if record is None:
        view.update(status="NOT_RUN", verdict=None, key_finding={
            "title": "No verdict: the evaluator did not run",
            "explanation": unavailable_reason or "The evaluator is unavailable."},
            findings=[], uncertainty=None, tags=None, outcome=None, unverified_commitments=[], routing=None,
            action="Configure the evaluator on the server (GEMINI_API_KEY) and evaluate again. Nothing here is a "
                   "pass.", record=None)
        view["transcript"] = _transcript(ni, cited)
        return view
    if record.record_status.value == "EVALUATION_FAILED":
        reason = record.failure.reason if record.failure else "unknown"
        view.update(status="EVALUATION_FAILED", verdict={"code": None, "label": "Evaluation Failed",
                                                         "display": "Evaluation Failed - no verdict"},
                    key_finding={"title": "The evaluation failed; no verdict was produced",
                                 "explanation": redact(reason)[:600]},
                    findings=[], uncertainty=None, tags=None, outcome=None, unverified_commitments=[], routing=None,
                    action="Re-run the evaluation or review the call manually. A failed evaluation is never a pass.",
                    record=record.model_dump(mode="json"))
        view["transcript"] = _transcript(ni, cited)
        return view

    for g in record.gates:
        info = cat.get(g.gate.value, {})
        if g.status.value == "FAIL":
            evs = [_ev(e) for e in g.evidence]
            cited.update(e["turn"] for e in evs if e["turn"])
            findings.append({
                "code": g.gate.value, "name": info.get("name"), "dimension": "Hard gate", "is_gate": True,
                "severity": "CRITICAL", "status": f"FAIL - {g.critical_status.value if g.critical_status else ''}",
                "confidence": g.confidence.value if g.confidence else None, "sub_rule": g.sub_rule,
                "explanation": info.get("description"), "evidence": evs,
                "evidence_unverified": g.evidence_unverified,
                "attribution": _attr(g.attribution), "action_type": "REVIEW / REMEDIATE"})
        elif g.status.value == "INCONCLUSIVE":
            inconclusive.append({"code": g.gate.value, "name": info.get("name"), "is_gate": True,
                                 "reasons": _reason_text([r.value for r in g.reason_codes]) or "not decidable from "
                                 "the available evidence"})
        elif g.status.value == "OUT_OF_SCOPE":
            out_of_scope.append(_oos(g.gate.value, info, g.oos_reason.value if g.oos_reason else None))
    for f in record.findings:
        info = cat.get(f.code, {})
        evs = [_ev(e) for e in f.evidence]
        cited.update(e["turn"] for e in evs if e["turn"])
        findings.append({
            "code": f.code, "name": info.get("name"), "dimension": info.get("dimension_name"), "is_gate": False,
            "severity": f.severity.value, "status": f.finding_state.value, "confidence": f.confidence.value,
            "sub_rule": f.sub_rule, "explanation": f.description or info.get("description"), "evidence": evs,
            "evidence_unverified": f.evidence_unverified, "attribution": _attr(f.attribution),
            "action_type": f.action_type.value, "repair_status": f.repair_status.value})
    for c in record.checks:
        info = cat.get(c.code, {})
        if c.status.value == "INCONCLUSIVE":
            inconclusive.append({"code": c.code, "name": info.get("name"), "is_gate": False,
                                 "reasons": _reason_text([r.value for r in c.reason_codes]) or
                                 "a required threshold or lexicon is pending sign-off, or the evidence is missing"})
        elif c.status.value == "OUT_OF_SCOPE":
            out_of_scope.append(_oos(c.code, info, c.oos_reason.value if c.oos_reason else None))
    for o in record.out_of_scope:
        out_of_scope.append(_oos(o.code, cat.get(o.code, {}), o.oos_reason.value))
    findings.sort(key=lambda x: (not x["is_gate"], SEVERITY_RANK.get(x["severity"], 9), x["status"] != "ASSERTED",
                                 {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(x["confidence"] or "", 3)))
    v = record.verdict.value if record.verdict else None
    ev_status = record.evaluability.status.value if record.evaluability else None
    tags = record.tags
    commitments = [_ev(e) for e in record.unverified_agent_commitments]
    view.update(
        status="OK",
        verdict={"code": v, "label": VERDICT_LABELS.get(v or "", v), "display": f"{VERDICT_LABELS.get(v or '', v)}"
                 f"{suffix}", "critical_status": record.critical_status.value if record.critical_status else None},
        key_finding=_key_finding(findings, v, record, ni),
        findings=findings,
        uncertainty={"evaluability": ev_status,
                     "reason_codes": [r.value for r in record.evaluability.reason_codes] if record.evaluability else [],
                     "reason_text": _reason_text([r.value for r in record.evaluability.reason_codes])
                     if record.evaluability else "",
                     "within_scope_complete": record.within_scope_complete,
                     "confidence_source": record.confidence_source.value if record.confidence_source else None,
                     "inconclusive": inconclusive, "out_of_scope": out_of_scope,
                     "note": "Checks that could not be decided are listed as INCONCLUSIVE or OUT OF SCOPE; missing "
                             "evidence is never counted as a pass."},
        tags={"dangerous_win": tags.dangerous_win.value if tags else "NONE",
              "clean_loss": bool(tags and tags.clean_loss),
              "dangerous_win_text": _dw_text(tags.dangerous_win.value if tags else "NONE"),
              "clean_loss_text": ("No payment outcome, but the agent behaved correctly and the outcome was driven by "
                                  "the customer or by policy (Clean Loss).") if tags and tags.clean_loss else
              "Not a Clean Loss.", "high_friction": "PENDING (threshold B-14)"},
        outcome=(record.outcome.model_dump(mode="json") if record.outcome else None),
        unverified_commitments=commitments,
        routing={"tier": record.routing_tier, "label": ROUTING_LABELS.get(record.routing_tier or 0,
                 "No routing tier: the spec assigns none here (a NOT EVALUABLE call, or only REVIEW-type findings)")},
        action=_action(v, record, findings),
        record=record.model_dump(mode="json"))
    view["transcript"] = _transcript(ni, cited | {e["turn"] for e in commitments if e["turn"]})
    return view


def _oos(code: str, info: dict[str, Any], reason: str | None) -> dict[str, Any]:
    r = reason or "MODE_CAPABILITY"
    return {"code": code, "name": info.get("name"), "reason_code": r, "reason": OOS_LABELS.get(r, r)}


def _attr(a: Any) -> dict[str, Any] | None:
    if a is None:
        return None
    return {"primary": a.primary.value, "label": ATTRIBUTION_LABELS.get(a.primary.value, a.primary.value),
            "secondary": a.secondary.value if a.secondary else None, "basis": a.basis.value, "notes": list(a.notes)}


def _dw_text(dw: str) -> str:
    return {"CRITICAL": "Dangerous Win (critical): a positive outcome was obtained while a hard gate fired.",
            "MATERIAL": "Dangerous Win (material): a positive outcome followed a major defect that can induce it.",
            }.get(dw, "No Dangerous Win.")


def _key_finding(findings: list[dict], verdict: str | None, record: EvaluationRecord,
                 ni: NormalizedInput) -> dict[str, Any]:
    if findings:
        f = findings[0]
        turns = [e["turn"] for e in f["evidence"] if e.get("turn")]
        return {"code": f["code"], "title": f"{f['code']} - {f['name']}", "severity": f["severity"],
                "status": f["status"], "explanation": f["explanation"], "turns": turns}
    if verdict == "NOT_EVALUABLE":
        codes = [r.value for r in record.evaluability.reason_codes] if record.evaluability else []
        return {"title": "The call could not be evaluated", "explanation": _reason_text(codes).capitalize() or
                "The front end found the call not evaluable.", "turns": []}
    return {"title": "No defects found within the available evidence",
            "explanation": "No gate fired and no finding was asserted. Undecided checks are listed under "
                           "uncertainty.", "turns": []}


def _action(verdict: str | None, record: EvaluationRecord, findings: list[dict]) -> str:
    if verdict == "CRITICAL_FAIL":
        return ("Escalate now: review the call and the customer impact, contact the customer if needed, and "
                "correct the agent's configuration before it repeats.")
    if verdict == "NEEDS_ATTENTION":
        return "Queue for remediation: review the cited turns and correct the agent behaviour."
    if verdict == "NOT_EVALUABLE":
        return "No conduct judgement is possible. Check the recording or transcript source."
    return "No action required; eligible for the audit sample."


def _transcript(ni: NormalizedInput, cited: set[int]) -> list[dict[str, Any]]:
    return [{"turn": t.turn, "role": t.role.value, "text": t.text, "cited": t.turn in cited,
             "unreliable": t.unreliable} for t in ni.turns]


def _evaluator_info(record: EvaluationRecord | None, source: str, model_called: bool) -> dict[str, Any]:
    pc = provider_config.describe()
    if record is not None and not model_called and record.record_status.value == "OK":
        provider, model = "none: deterministic front end (no model call was needed)", "-"
    else:
        provider = pc["provider"] if source == LIVE else ("scripted replay" if source == REPLAY else pc["provider"])
        model = (record.system.model_snapshot_id if record and record.system.model_snapshot_id else
                 (DEMO_MODEL_ID if source == REPLAY else pc["model_id"]))
    return {"system": "B", "system_version": EvaluatorB.version, "provider": provider, "model": model,
            "model_called": model_called,
            "prompt_version": PROMPT_TEMPLATE_VERSION, "engine_version": ENGINE_VERSION,
            "frontend_version": FRONTEND_VERSION, "temperature": pc["temperature"],
            "input_modality": pc["input_modality"]}


# ------------------------------------------------------------------------------------------ service
class ReviewService:
    def __init__(self, spec: Spec, *, root: Path | None = None, report_path: Path | None = None,
                 sleep: Callable[[float], None] | None = None):
        self.spec = spec
        self._sleep = sleep  # transport-retry backoff (tests pass a no-op)
        self.root = root or repo_root()
        self.report_path = report_path or Path(os.environ.get(REPORT_ENV) or
                                               self.root / "reports" / "dev-baseline" / "dev-baseline.json")
        self.demos = {d["id"]: d for d in load_demo_calls()}
        self._lock = threading.Lock()
        self._library: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._counter = 0
        for d in self.demos.values():  # the library starts with the demo calls' replayed results
            self._store(self._run(EvaluateRequest(mode=Mode.TRANSCRIPT.value, transcript_text=d["transcript"],
                                                  demo_id=d["id"], replay=True, call_name=d["title"])))

    # -- protected paths: the evaluator may not read gold, cards, the design, registries, private data or runs
    def protected_paths(self) -> list[Path]:
        layout = BenchLayout(self.root / "bench")
        extra = [self.root / d for d in ("runs", "dev_draft_runs", "scoring", "blinding", "blinded")]
        return layout.protected_paths() + extra + [self.root / "bench" / "dev" / "transcripts" / "provenance.yaml"]

    def live_available(self) -> bool:
        return provider_config.api_key_configured() and provider_config.model_id_problem(
            provider_config.settings().model_id) is None

    # -- intake
    def _parse(self, req: EvaluateRequest) -> ParsedTranscript:
        raw: str | None = None
        name = (req.transcript_filename or "").lower()
        if req.transcript_bytes:
            if len(req.transcript_bytes) > MAX_TRANSCRIPT_BYTES:
                raise AppError(413, "TRANSCRIPT_TOO_LARGE", f"The transcript is larger than "
                                                            f"{MAX_TRANSCRIPT_BYTES // 1024} KB.")
            if not name.endswith((".txt", ".json")):
                raise AppError(400, "TRANSCRIPT_FORMAT", "Upload the transcript as .txt or .json (R-15).")
            try:
                raw = req.transcript_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise AppError(400, "TRANSCRIPT_ENCODING", "The transcript must be UTF-8 text.") from None
        elif req.transcript_text and req.transcript_text.strip():
            raw = req.transcript_text
            if len(raw.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
                raise AppError(413, "TRANSCRIPT_TOO_LARGE", f"The transcript is larger than "
                                                            f"{MAX_TRANSCRIPT_BYTES // 1024} KB.")
        if raw is None:
            raise AppError(400, "TRANSCRIPT_MISSING", "Paste a transcript, upload a .txt / .json file, or pick a "
                                                      "demo call.")
        as_json = name.endswith(".json") or (not name and raw.lstrip().startswith("{"))
        try:
            parsed = parse_json(raw) if as_json else parse_txt(raw)
        except TranscriptParseError as exc:
            raise AppError(400, "TRANSCRIPT_INVALID", f"The transcript could not be read: {exc}", [
                "Label every line AGENT: or BORROWER: (CUSTOMER: also works).",
                "Optional header lines before the turns: # call_start_ts: 2026-09-28T14:05:00+05:30"]) from None
        if not parsed.turns:
            raise AppError(400, "TRANSCRIPT_EMPTY", "The transcript has no turns.")
        if len(parsed.turns) > MAX_TURNS:
            raise AppError(413, "TRANSCRIPT_TOO_LONG", f"The transcript has more than {MAX_TURNS} turns.")
        return parsed

    def _normalize(self, req: EvaluateRequest) -> tuple[NormalizedInput, Mode]:
        try:
            mode = Mode(req.mode)
        except ValueError:
            raise AppError(400, "MODE_INVALID", "Mode must be audio, transcript or audio_transcript.") from None
        if mode is Mode.AUDIO:
            has_transcript = bool(req.transcript_bytes or (req.transcript_text or "").strip())
            raise AppError(422, "ASR_PENDING", f"{MODE_INFO[Mode.AUDIO]['note']} No result is produced for "
                           "audio alone rather than a guessed one.", [
                               "Use Audio + Transcript with the call's transcript (the platform or a human "
                               "transcript)." if not has_transcript else
                               "You supplied a transcript: switch to Audio + Transcript to evaluate it with the audio.",
                               "Or use Transcript mode.",
                               "Audio-only evaluation arrives with the ASR / diarization decision (B-06)."])
        parsed = self._parse(req)
        audio = None
        if mode is Mode.AUDIO_TRANSCRIPT:
            if not req.audio_bytes:
                raise AppError(400, "AUDIO_MISSING", "Audio + Transcript needs the audio file as well.")
            if len(req.audio_bytes) > MAX_AUDIO_BYTES:
                raise AppError(413, "AUDIO_TOO_LARGE", f"The audio is larger than {MAX_AUDIO_BYTES // 2**20} MB.")
            ext = Path(req.audio_filename or "").suffix.lower().lstrip(".")
            if ext not in AUDIO_FORMATS:
                raise AppError(400, "AUDIO_FORMAT", "Audio must be .wav, .mp3 or .m4a.")
            audio = audio_ref_from_bytes(req.audio_bytes, ext)  # hashed in memory; the bytes are not stored
        try:
            unit = UnitMode.A_T if mode is Mode.AUDIO_TRANSCRIPT else UnitMode.TRANSCRIPT
            return normalize_supplied(parsed, unit, self.spec, audio=audio), mode
        except NormalizationError as exc:
            raise AppError(400, "NORMALIZATION", str(exc)) from None

    # -- evaluation
    def _demo_for(self, req: EvaluateRequest) -> dict[str, Any] | None:
        d = self.demos.get(req.demo_id or "")
        if d is None or req.transcript_bytes:
            return None
        return d if _same_text(req.transcript_text or "", d["transcript"]) else None

    def _run(self, req: EvaluateRequest) -> dict[str, Any]:
        ni, mode = self._normalize(req)
        demo = self._demo_for(req)
        use_replay = demo is not None and (req.replay or not self.live_available())
        trace = TraceSink()
        t0 = time.perf_counter()
        record: EvaluationRecord | None = None
        unavailable: str | None = None
        if not_evaluable_short_circuit(ni):  # deterministic: no model call needed, so it is live even without a key
            record = short_circuit_record(ni, self.spec, _b_info(), ConfidenceSource.COMPUTED)
            source = REPLAY if use_replay else LIVE
        elif use_replay:
            assert demo is not None
            record = self._evaluate(ScriptedDemoClient(demo["script"]), ni, trace)
            source = REPLAY
        elif self.live_available():
            source = LIVE
            try:
                record = self._evaluate(GeminiClient(), ni, trace)
            except ProviderConfigError as exc:  # e.g. an invalid GEMINI_MODEL: no call was made
                source, unavailable = UNAVAILABLE, redact(str(exc))
        else:
            source = UNAVAILABLE
            unavailable = ("Live evaluation is not configured on this server: the runtime environment variable "
                           f"{provider_config.API_KEY_ENV} is not set. Demo calls can still be replayed.")
        name = (req.call_name or "").strip()[:80] or (demo["title"] if demo else f"Evaluated call {self._next()}")
        call = {"name": name, "mode": mode.value, "mode_label": MODE_INFO[mode]["label"],
                "unit_mode": ni.unit_mode.value, "n_turns": len(ni.turns), "has_audio": ni.audio is not None,
                "demo": demo is not None, "demo_id": demo["id"] if demo else None,
                "scenario": demo["scenario"] if demo else None}
        view = result_view(record, ni, self.spec, source=source, call=call, trace=trace,
                           latency_s=time.perf_counter() - t0, unavailable_reason=unavailable)
        view["evaluation_id"] = f"ev-{uuid.uuid4().hex[:12]}"
        view["created_at"] = datetime.now(timezone.utc).isoformat()
        return view

    def _evaluate(self, client: LLMClient, ni: NormalizedInput, trace: TraceSink) -> EvaluationRecord:
        b = EvaluatorB(client, self.spec, seed=provider_config.SEED if client.backend == "gemini" else None,
                       sleep=self._sleep)
        try:
            with ProtectedPathGuard(self.protected_paths()):
                rec = b.evaluate(ni, EvaluationContext(repetition=1, spec=self.spec, trace=trace))
            return EvaluationRecord.model_validate(rec.model_dump(mode="json"))
        except LLMUnavailableError as exc:
            log.warning("provider unreachable after retries: %s", redact(str(exc)))
            return failed_record(ni, self.spec, b.system_info(), f"provider unreachable after "
                                 f"{b.transport.max_retries} retries: {redact(str(exc))}", 0)
        except ProviderRequestError as exc:
            log.warning("provider error: %s", redact(str(exc)))
            return failed_record(ni, self.spec, b.system_info(), f"provider error: {redact(str(exc))}", 0)

    def evaluate(self, req: EvaluateRequest) -> dict[str, Any]:
        view = self._run(req)
        self._store(view)
        return view

    # -- library
    def _next(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def _store(self, view: dict[str, Any]) -> None:
        with self._lock:
            self._library[view["evaluation_id"]] = view
            while len(self._library) > LIBRARY_LIMIT:
                self._library.popitem(last=False)

    def library(self) -> list[dict[str, Any]]:
        with self._lock:
            views = list(self._library.values())
        rows = []
        for v in reversed(views):
            kf = v.get("key_finding") or {}
            rows.append({"evaluation_id": v["evaluation_id"], "name": v["call"]["name"],
                         "modality": v["call"]["mode_label"], "verdict": (v.get("verdict") or {}).get("label"),
                         "verdict_code": (v.get("verdict") or {}).get("code"), "primary_finding": kf.get("title"),
                         "status": v["status"], "source": v["source"], "source_label": v["source_label"],
                         "evaluability": (v.get("uncertainty") or {}).get("evaluability"),
                         "dangerous_win": (v.get("tags") or {}).get("dangerous_win"),
                         "clean_loss": (v.get("tags") or {}).get("clean_loss"), "created_at": v["created_at"]})
        return rows

    def get(self, evaluation_id: str) -> dict[str, Any]:
        with self._lock:
            v = self._library.get(evaluation_id)
        if v is None:
            raise AppError(404, "NOT_FOUND", "No evaluation with that id (the library is kept in memory and resets "
                                             "when the server restarts).")
        return v

    # -- read-only panels
    def config(self) -> dict[str, Any]:
        pc = provider_config.describe()
        return {
            "product": {"name": "Ignosis Call Quality Judge",
                        "tagline": "Ignosis already listens to every call. This layer judges whether the AI agent "
                                   "behaved correctly.",
                        "primary_user": "AI Quality Reviewer / Operations QA Reviewer", "version": PACKAGE_VERSION},
            "live_available": self.live_available(),
            "evaluator": {**pc, "system": "B", "system_version": EvaluatorB.version,
                          "prompt_version": PROMPT_TEMPLATE_VERSION, "engine_version": ENGINE_VERSION,
                          "frontend_version": FRONTEND_VERSION},
            "modes": [{"id": m.value, **info} for m, info in MODE_INFO.items()],
            "limits": {"transcript_kb": MAX_TRANSCRIPT_BYTES // 1024, "audio_mb": MAX_AUDIO_BYTES // 2**20,
                       "audio_formats": list(AUDIO_FORMATS)},
            "profile": profile_panel(self.spec),
        }

    def demo_calls(self) -> list[dict[str, Any]]:
        return [{k: d[k] for k in ("id", "title", "scenario", "modality", "transcript")} for d in self.demos.values()]

    def reliability(self) -> dict[str, Any]:
        return reliability_view(self.report_path)


def _b_info() -> SystemInfo:
    return SystemInfo(system="B", version=EvaluatorB.version)


def profile_panel(spec: Spec) -> dict[str, Any]:
    p, r = spec.profile, spec.rubric
    pending = find_pending(spec.rubric, spec.profile)
    return {
        "profile_id": spec.profile_id, "profile_version": spec.profile_version, "use_case": p.get("use_case"),
        "disclaimer": p.get("disclaimer"), "rubric_version": spec.rubric_version,
        "contract_version": spec.contract_version,
        "settings": [
            {"name": "Identity verification", "value": str(p["identity_verification"]["minimum"]).replace("_", " ")
             + " before any account disclosure"},
            {"name": "Promise to pay", "value": "needs " + " and ".join(p["ptp"]["required_fields"]) +
             ("; read-back required" if p["ptp"].get("readback_required") else "")},
            {"name": "Offers the AI agent may not make", "value": ", ".join(p["offers"]["not_ai_authorized"])},
            {"name": "Offers with unknown authority", "value": ", ".join(p["offers"]["authority_unknown"])},
            {"name": "Escalation triggers", "value": ", ".join(p["escalation_triggers"]["triggers"])},
            {"name": "Calling window", "value": f"{p['calling_window']['start']}-{p['calling_window']['end']} "
             f"{p['calling_window']['timezone']} (needs call_start_ts in the transcript header)"},
            {"name": "Languages", "value": ", ".join(p["languages_supported"])},
            {"name": "Max asks after refusal or inability", "value": str(p["asks"]["max_after_explicit_refusal_or_inability"])},
        ],
        "gates": [{"id": g["id"], "name": g["name"]} for g in r["gates"]],
        "pending_signoff": {"count": len(pending), "blocking": sorted({x.blocker for x in pending})},
        "read_only": True,
    }


def reliability_view(report_path: Path) -> dict[str, Any]:
    pc = provider_config.describe()
    base: dict[str, Any] = {
        "label": "DEV ENGINEERING MEASUREMENT - NOT FINAL RELIABILITY EVIDENCE",
        "final_validation": "FINAL RELIABILITY VALIDATION: PENDING",
        "evaluator": {"system": "B (A and A+ are measured for comparison)", "system_version": EvaluatorB.version,
                      "provider": pc["provider"], "model": pc["model_id"], "prompt_version": PROMPT_TEMPLATE_VERSION,
                      "engine_version": ENGINE_VERSION, "live_available": pc["api_key_configured"]},
        "gold": "none: gold labelling is pending (B-02); nothing here is scored against gold",
        "holdout": "not started", "red_team": "not started",
    }
    if not report_path.exists():
        return {**base, "status": "NO_DEV_REPORT", "message": "No DEV baseline report is available on this server."}
    rep = json.loads(report_path.read_text(encoding="utf-8"))
    systems = {}
    unmeasured: list[str] = []
    for name, m in rep.get("systems", {}).items():
        if m.get("status") != "EXECUTED":
            systems[name] = {"status": "NOT EXECUTED", "reason": m.get("reason")}
            continue
        vals = {}
        for key in ("verdict_accuracy", "critical_recall", "must_not_fire_precision", "pair_accuracy",
                    "defect_precision", "defect_recall", "evidence_faithfulness", "attribution_agreement",
                    "dangerous_win_agreement", "clean_loss_agreement"):
            x = m.get(key)
            if not isinstance(x, dict):
                continue
            vals[key] = {"value": x.get("value"), "num": x.get("num"), "den": x.get("den")}
            if x.get("value") in (None, "UNMEASURED"):
                unmeasured.append(f"{name}: {key.replace('_', ' ')}")
        cons = m.get("consistency") or {}
        if cons.get("value") == "UNMEASURED":
            unmeasured.append(f"{name}: consistency ({cons.get('why', 'one repetition')})")
        systems[name] = {"status": "EXECUTED", "metrics": vals, "abstention": m.get("abstention"),
                         "latency_s": m.get("latency_s"), "tokens": m.get("tokens"), "cost": m.get("cost")}
    run = rep.get("run", {})
    return {**base, "status": "OK", "report_version": rep.get("report_version"),
            "run": {"run_id": run.get("run_id"), "created_at": run.get("created_at"),
                    "repetitions": run.get("repetitions"), "unit_mode": run.get("unit_mode"),
                    "asr_mode": run.get("asr_mode"), "git_commit": (run.get("git_commit") or "")[:12]},
            "benchmark": {"split": "DEV (drafts, human review pending)",
                          "items_executed": len(run.get("items_executed", [])),
                          "items_excluded": len(run.get("items_excluded", {})),
                          "transcripts": rep.get("transcripts", {}).get("status"),
                          "human_review_pending": rep.get("transcripts", {}).get("human_review_pending"),
                          "reference": rep.get("reference")},
            "report_evaluator_configuration": rep.get("evaluator_configuration"),
            "systems": systems, "summary_table": rep.get("summary_table"), "comparison": rep.get("comparison"),
            "frontend": {k: rep.get("frontend", {}).get(k) for k in ("evaluability_agreement", "g7_agreement")},
            "reproducibility": rep.get("consistency_smoke"), "unmeasured": unmeasured}


__all__ = ["AppError", "EvaluateRequest", "Mode", "ReviewService", "ScriptedDemoClient", "catalog",
           "load_demo_calls", "profile_panel", "reliability_view", "result_view"]
