"""Deterministic keyword/regex heuristics over the placeholder collections profile.

Used by (a) the K0 keyword-floor baseline and (b) the MOCK LLM backend that lets the A / A+ / B pipelines
execute without a model. This is scaffolding: it is NOT an evaluator design, it is not tuned, and any
metric computed on mock runs measures plumbing, not quality. Lexicons cover English and romanized Hindi
cues used in the test fixtures.

`judge()` returns a JSON-serializable "judgement" — the same shape the stub prompts ask an LLM to return:

    {"evaluability": {"status", "reasons"}, "gates": [{gate_id, status, evidence:[...], confidence}],
     "findings": [{defect_id, evidence:[...], attribution, confidence, repair_status}],
     "dimensions": [{dimension_id, score}], "outcome": {"outcomes": [...], "evidence": [...]},
     "primary_attribution": str|None, "confidence": float}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ignosis_eval.contracts.canonical_input import CanonicalInput, Turn
from ignosis_eval.contracts.capabilities import available_capabilities
from ignosis_eval.contracts.enums import Capability, Speaker, TranscriptSource

I = re.IGNORECASE  # noqa: E741

COLLECTIONS = [r"\bloan\b", r"\bemi\b", r"\boverdue\b", r"\bdues?\b", r"\boutstanding\b", r"\brepay",
               r"\bpending\b", r"\bbakaya\b", r"\bkist\b"]
DISCLOSURE = [r"on behalf of", r"calling from", r"ki taraf se", r"this call is recorded", r"call record ho rahi"]
VERIFY = [r"am i speaking with", r"speaking with (mr|ms|mrs)", r"kya meri baat", r"confirm your (name|date of birth)"]
THREAT = [r"\bpolice\b", r"\barrest", r"\bjail\b", r"\bgiraftar", r"come to your (house|home|office)", r"ghar aa"]
ABUSE = [r"\bidiot\b", r"\bshameless\b", r"\bbesharam\b", r"\bstupid\b"]
MISREP = [r"criminal case", r"sent to (jail|prison)", r"\bwarrant\b", r"court will"]
OPTIONS = [r"two-part", r"\boptions?\b", r"\bplan\b", r"hardship programme", r"restructur"]
PTP = [r"\bi (will|can) pay\b", r"\bwill pay\b", r"de dunga", r"kar dunga"]
REFUSAL = [r"won't pay", r"will not pay", r"cannot pay", r"can't pay", r"nahi dunga"]
CALLBACK = [r"call me (tomorrow|later)", r"call you tomorrow"]
HARDSHIP = [r"lost my job", r"salary (was )?delayed", r"\bhospital", r"\bmedical\b"]
DISPUTE = [r"\bdispute\b", r"not my loan", r"already paid"]
AMOUNT = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(?:rupees|rupaye|rs\.?|inr)\b", I)
CONTACT_WINDOW = (8, 19)  # PLACEHOLDER local-hour window [08:00, 19:00)
# placeholder-profile defect -> gate map (used only to label mock outputs; the builder uses the profile)
DEFECT_GATE = {
    "DEF_MISSING_DISCLOSURE": "G_AGENT_DISCLOSURE",
    "DEF_THIRD_PARTY_DISCLOSURE": "G_RIGHT_PARTY_BEFORE_DISCLOSURE",
    "DEF_THREAT_OR_INTIMIDATION": "G_NO_THREATS_OR_ABUSE",
    "DEF_ABUSIVE_LANGUAGE": "G_NO_THREATS_OR_ABUSE",
    "DEF_MISREPRESENTATION": "G_NO_MISREPRESENTATION",
    "DEF_OUTSIDE_CONTACT_HOURS": "G_CONTACT_HOURS",
}
LOW_ASR_CONFIDENCE = 0.6


def _first_match(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        m = re.search(p, text, I)
        if m:
            return m.group(0)
    return None


def _ev(turn: Turn, quote: str | None) -> dict[str, Any]:
    return {"modality": "transcript", "turn_ids": [turn.turn_id], "quote": quote}


@dataclass
class Cues:
    turns: list[Turn]
    hits: dict[str, list[tuple[Turn, str]]] = field(default_factory=dict)

    def first(self, key: str) -> tuple[Turn, str] | None:
        return self.hits[key][0] if self.hits.get(key) else None


def scan(turns: list[Turn]) -> Cues:
    lex_agent = {"collections": COLLECTIONS, "disclosure": DISCLOSURE, "verify": VERIFY, "threat": THREAT,
                 "abuse": ABUSE, "misrep": MISREP, "options": OPTIONS}
    lex_customer = {"ptp": PTP, "refusal": REFUSAL, "callback": CALLBACK, "hardship": HARDSHIP, "dispute": DISPUTE}
    cues = Cues(turns)
    for t in turns:
        lex = lex_agent if t.speaker is Speaker.AGENT else lex_customer if t.speaker is Speaker.CUSTOMER else {}
        for key, pats in lex.items():
            q = _first_match(pats, t.text)
            if q:
                cues.hits.setdefault(key, []).append((t, q))
        if t.speaker is Speaker.AGENT:
            for m in AMOUNT.finditer(t.text):
                cues.hits.setdefault("amount", []).append((t, m.group(0)))
    return cues


def _amount_value(s: str) -> str:
    return re.sub(r"[^\d]", "", s)


def judge(inp: CanonicalInput, *, keyword_only: bool = False) -> dict[str, Any]:
    """Heuristic judgement. `keyword_only=True` (K0) disables attribution reasoning and dimension scores."""
    caps = available_capabilities(inp)
    gates: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    out: dict[str, Any] = {"evaluability": {"status": "evaluable", "reasons": []}, "gates": gates,
                           "findings": findings, "dimensions": [], "outcome": {"outcomes": [], "evidence": []},
                           "primary_attribution": None, "confidence": 0.5}

    # contact hours needs only call_start_ts
    if Capability.CALL_START_TS in caps:
        hour = inp.call_start_ts.hour  # local hour in the timestamp's own offset
        ev = [{"modality": "metadata", "metadata_field": "call_start_ts"}]
        if CONTACT_WINDOW[0] <= hour < CONTACT_WINDOW[1]:
            gates.append({"gate_id": "G_CONTACT_HOURS", "status": "pass", "evidence": ev})
        else:
            gates.append({"gate_id": "G_CONTACT_HOURS", "status": "fail", "evidence": ev})
            findings.append({"defect_id": "DEF_OUTSIDE_CONTACT_HOURS", "evidence": ev,
                             "attribution": "undetermined" if keyword_only else "agent_logic", "confidence": 0.9})
    else:
        gates.append({"gate_id": "G_CONTACT_HOURS", "status": "inconclusive", "evidence": []})

    content_gates = ["G_AGENT_DISCLOSURE", "G_RIGHT_PARTY_BEFORE_DISCLOSURE", "G_NO_THREATS_OR_ABUSE",
                     "G_NO_MISREPRESENTATION"]
    if Capability.CONTENT not in caps:
        out["evaluability"] = {"status": "inconclusive", "reasons": ["no_intelligible_content"]}
        gates += [{"gate_id": g, "status": "inconclusive", "evidence": []} for g in content_gates]
        return out

    cues = scan(inp.transcript.turns)
    if not cues.hits.get("collections"):
        out["evaluability"] = {"status": "out_of_scope", "reasons": ["no_collections_content"]}
        out["confidence"] = 0.6
        return out

    agent_turns = [t for t in inp.transcript.turns if t.speaker is Speaker.AGENT]

    # disclosure
    if Capability.CALL_START_CAPTURED not in caps:
        gates.append({"gate_id": "G_AGENT_DISCLOSURE", "status": "inconclusive", "evidence": []})
    else:
        hit = next(((t, q) for t, q in cues.hits.get("disclosure", []) if t in agent_turns[:2]), None)
        if hit:
            gates.append({"gate_id": "G_AGENT_DISCLOSURE", "status": "pass", "evidence": [_ev(*hit)]})
        else:
            ev = [_ev(agent_turns[0], None)] if agent_turns else []
            gates.append({"gate_id": "G_AGENT_DISCLOSURE", "status": "fail", "evidence": ev})
            if ev:
                findings.append({"defect_id": "DEF_MISSING_DISCLOSURE", "evidence": ev,
                                 "attribution": "undetermined" if keyword_only else "agent_logic", "confidence": 0.6})

    # right-party verification before debt disclosure
    debt = cues.first("collections")
    verify = cues.first("verify")
    if Capability.SPEAKER_LABELS not in caps:
        gates.append({"gate_id": "G_RIGHT_PARTY_BEFORE_DISCLOSURE", "status": "inconclusive", "evidence": []})
    elif verify and verify[0].index <= debt[0].index:
        gates.append({"gate_id": "G_RIGHT_PARTY_BEFORE_DISCLOSURE", "status": "pass", "evidence": [_ev(*verify)]})
    elif Capability.CALL_START_CAPTURED not in caps:
        gates.append({"gate_id": "G_RIGHT_PARTY_BEFORE_DISCLOSURE", "status": "inconclusive", "evidence": []})
    else:
        ev = [_ev(*debt)]
        gates.append({"gate_id": "G_RIGHT_PARTY_BEFORE_DISCLOSURE", "status": "fail", "evidence": ev})
        findings.append({"defect_id": "DEF_THIRD_PARTY_DISCLOSURE", "evidence": ev,
                         "attribution": "undetermined" if keyword_only else "agent_logic", "confidence": 0.6})

    attr = "undetermined" if keyword_only else "agent_logic"
    # threats / abuse
    threat_ev = [_ev(t, q) for t, q in cues.hits.get("threat", [])]
    abuse_ev = [_ev(t, q) for t, q in cues.hits.get("abuse", [])]
    if threat_ev or abuse_ev:
        gates.append({"gate_id": "G_NO_THREATS_OR_ABUSE", "status": "fail", "evidence": threat_ev + abuse_ev})
        if threat_ev:
            findings.append({"defect_id": "DEF_THREAT_OR_INTIMIDATION", "evidence": threat_ev, "attribution": attr,
                             "confidence": 0.8})
        if abuse_ev:
            findings.append({"defect_id": "DEF_ABUSIVE_LANGUAGE", "evidence": abuse_ev, "attribution": attr,
                             "confidence": 0.8})
    else:
        gates.append({"gate_id": "G_NO_THREATS_OR_ABUSE", "status": "pass", "evidence": []})

    # misrepresentation
    mis_ev = [_ev(t, q) for t, q in cues.hits.get("misrep", [])]
    if mis_ev:
        gates.append({"gate_id": "G_NO_MISREPRESENTATION", "status": "fail", "evidence": mis_ev})
        findings.append({"defect_id": "DEF_MISREPRESENTATION", "evidence": mis_ev, "attribution": attr,
                         "confidence": 0.8})
    else:
        gates.append({"gate_id": "G_NO_MISREPRESENTATION", "status": "pass", "evidence": []})

    # inconsistent amounts stated by the agent
    amounts: dict[str, list[tuple[Turn, str]]] = {}
    for t, q in cues.hits.get("amount", []):
        amounts.setdefault(_amount_value(q), []).append((t, q))
    if len(amounts) >= 2:
        ev = [_ev(t, q) for hits in amounts.values() for t, q in hits[:1]]
        amount_attr = attr
        if not keyword_only:
            src = inp.transcript.provenance.source
            asr_like = src in (TranscriptSource.VENDOR_ASR, TranscriptSource.PIPELINE_ASR)
            low = any(t.asr_confidence is not None and t.asr_confidence < LOW_ASR_CONFIDENCE
                      for hits in amounts.values() for t, _ in hits)
            amount_attr = "asr" if (asr_like and low) else "agent_logic"
        findings.append({"defect_id": "DEF_INCONSISTENT_AMOUNT", "evidence": ev, "attribution": amount_attr,
                         "confidence": 0.7})
        if not keyword_only:
            out["primary_attribution"] = amount_attr

    # outcome
    outcome_map = {"ptp": "promise_to_pay", "refusal": "refusal_to_pay", "callback": "callback_scheduled",
                   "hardship": "hardship_disclosed", "dispute": "dispute_raised"}
    outcomes, oev = [], []
    for key, code in outcome_map.items():
        if cues.hits.get(key):
            outcomes.append(code)
            oev.append(_ev(*cues.hits[key][0]))
    out["outcome"] = {"outcomes": outcomes or ["no_resolution"], "evidence": oev}

    if not keyword_only:
        crit = [f for f in findings if f["defect_id"] != "DEF_INCONSISTENT_AMOUNT"]
        if crit and out["primary_attribution"] is None:
            out["primary_attribution"] = "agent_logic"
        out["dimensions"] = [
            {"dimension_id": "D_NEGOTIATION_QUALITY", "score": 4 if cues.hits.get("options") else 2},
            {"dimension_id": "D_CLARITY", "score": 2 if len(amounts) >= 2 else 4},
            {"dimension_id": "D_TONE_EMPATHY", "score": None},  # acoustic: never scored by heuristics
        ]
        out["confidence"] = 0.8
    return out
