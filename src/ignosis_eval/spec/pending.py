"""Inventory of PENDING_HUMAN_SIGNOFF items (spec files) and run-configuration blockers (B-01..B-16).

`find_pending(spec)` walks rubric.yaml and profile.yaml for values that are exactly
`PENDING_HUMAN_SIGNOFF` (prose `production_note` strings that merely mention the marker are
production-only and are not assignment blockers). Each item is mapped to its blocker id from
implementation-blockers.md and to whether it blocks a locked (holdout / red-team) run.
Unknown pending items are treated as blocking (fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ignosis_eval.spec.loader import PENDING

# path -> (blocker id, blocks a locked run?, note). Paths use dotted keys; list items use [id].
KNOWN: dict[str, tuple[str, bool, str]] = {
    "profile.overall_signoff.status": ("B-11", True, "profile defaults must be confirmed before benchmark freeze"),
    "profile.consequences.approved_statement_wording.status": (
        "B-11", False, "optional; until provided J-CONS classifies against the categories only"),
    "profile.vulnerability_protocol.helpline.status": (
        "B-11", False, "until decided, a helpline reference is not required"),
    "profile.thresholds.loop_similarity.value": ("B-11", True, "RES-11"),
    "profile.thresholds.asr_low_confidence_word.value": ("B-06/B-11", True, "audio reliability (DC-01)"),
    "profile.thresholds.asr_unreliable_call_share.value": ("B-11", True, "audio whole-call NOT_EVALUABLE (DC-01)"),
    "profile.thresholds.material_span_min_confidence.value": ("B-11", True, "material-span reliability"),
    "profile.thresholds.non_conversation_min_borrower_words.value": ("B-11", True, "DC-02 / G1c pre-check"),
    "profile.thresholds.overlap_min_seconds.value": ("B-11", True, "PLT-02"),
    "profile.thresholds.diarization_turn_min_confidence.value": (
        "B-06/B-11", True, "turn-level diarization reliability (DC-01, AJ-05)"),
    "profile.thresholds.high_friction_min_minor_count.value": ("B-14", False, "HIGH_FRICTION tag only"),
    "profile.lexicons.review_status": ("B-04", True, "all lexicon `terms` are empty until native review"),
    "rubric.evaluability_checks[DC-LANG].detection": ("B-04/B-11", True, "romanized unsupported-language detection"),
    "rubric.platform_signals[PLT-01].severity_escalation": ("B-15", False, "PLT stays MINOR by default"),
    "rubric.platform_signals[PLT-02].severity_escalation": ("B-15", False, "PLT stays MINOR by default"),
    "rubric.platform_signals[PLT-03].severity_escalation": ("B-15", False, "PLT stays MINOR by default"),
    "rubric.outcome_model.primary_disposition_precedence": ("B-13", False, "display only, not scored"),
    "rubric.tags.high_friction.threshold": ("B-14", False, "tag only; does not affect the verdict"),
}

# Run-configuration blockers that are not values inside rubric/profile (implementation-blockers.md).
RUN_BLOCKERS: dict[str, str] = {
    "B-01": "benchmark case content",
    "B-02": "gold labels",
    "B-03": "red-team cases",
    "B-05": "LLM model snapshot id, API access, max_tokens, seed support",
    "B-06": "ASR / diarization choice",
    "B-07": "weaker ASR for platform-style transcripts",
    "B-08": "pricing snapshot",
    "B-09": "audio policy",
    "B-10": "labeling setup",
    "B-12": "tuning timebox",
    "B-16": "optional real calls",
}


@dataclass(frozen=True)
class PendingItem:
    path: str
    blocker: str | None
    blocks_locked_run: bool
    note: str
    decision_required: str | None = None


def blocker_for(path: str) -> str | None:
    entry = KNOWN.get(path)
    return entry[0] if entry else None


def _walk(node: Any, path: str, out: list[tuple[str, Any]], parent: Any = None) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _walk(v, f"{path}.{k}", out, node)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            key = v.get("id") if isinstance(v, dict) and "id" in v else i
            _walk(v, f"{path}[{key}]", out, node)
    elif node == PENDING:
        out.append((path, parent))


def find_pending(rubric: dict[str, Any], profile: dict[str, Any]) -> list[PendingItem]:
    found: list[tuple[str, Any]] = []
    _walk(rubric, "rubric", found)
    _walk(profile, "profile", found)
    items = []
    for path, parent in found:
        decision = parent.get("decision_required") if isinstance(parent, dict) else None
        blocker, blocks, note = KNOWN.get(path, (None, True, "unrecognised pending item (treated as blocking)"))
        items.append(PendingItem(path, blocker, blocks, note, decision))
    return sorted(items, key=lambda i: i.path)
