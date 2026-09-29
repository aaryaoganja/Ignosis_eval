"""Prompt assembly: hand-written stub instruction templates + a GENERATED rubric section.

experiment-protocol P-2: "The rubric text inside prompts is generated from rubric.yaml + profile.yaml by a
versioned template. Hand-written instruction text around the generated section is the tunable part."
Instruction templates are the files in evaluators/prompts/ (all marked UNOPTIMIZED STUB; not tuned).
A+ has no prompt of its own (§11).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from string import Template

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.spec.loader import Spec
from ignosis_eval.versions import PROMPT_TEMPLATE_VERSION

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
A_PROMPTS = ("a_holistic",)
B_PROMPTS = ("b_extraction", "b_judgments")


def load_prompt(name: str) -> tuple[str, str]:
    data = (PROMPT_DIR / f"{name}.md").read_bytes()
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest()


def _clean(s: object) -> str:
    return " ".join(str(s).split())


def rubric_section(spec: Spec) -> str:
    """Deterministic rendering of gates, codes and key policy values (template PROMPT_TEMPLATE_VERSION)."""
    reg = spec.registry
    lines = [f"[rubric {spec.rubric_version} | profile {spec.profile_id} {spec.profile_version} | "
             f"template {PROMPT_TEMPLATE_VERSION}]", "Gates (CRITICAL, non-compensatory):"]
    for gid in reg.gate_ids:
        raw = reg.get(gid).raw
        lines.append(f"- {gid} {raw['name']}: {_clean(raw['description'])}")
        for name, text in (raw.get("definitions") or {}).items():
            if isinstance(text, dict):  # e.g. G5 honoring_event per request type
                lines.append(f"  - {name}:")
                lines += [f"    - {k}: {_clean(v)}" for k, v in text.items()]
            else:
                lines.append(f"  - {name}: {_clean(text)}")
        for row in raw.get("decision_table") or []:  # ordered; first matching entry wins (G5, AJ-03)
            extra = ", ".join(f"{k}: {_clean(v)}" for k, v in row.items() if k not in ("order", "when", "result"))
            lines.append(f"  - {row['order']}. when {_clean(row['when'])} -> {row['result']}"
                         + (f" ({extra})" if extra else ""))
    lines.append("Codes:")
    for cid in reg.code_ids + reg.platform_ids:
        raw = reg.get(cid).raw
        sev = raw.get("severity") or raw.get("severity_rule") or raw.get("severity_default")
        lines.append(f"- {cid} {raw['name']} ({_clean(sev)}): {_clean(raw.get('description') or raw.get('rule'))}")
    p = spec.profile
    lines += [
        "Profile values:",
        f"- identity verification: {p['identity_verification']['minimum']} before "
        f"{', '.join(p['identity_verification']['must_precede'])}",
        f"- offers not AI-authorized: {', '.join(p['offers']['not_ai_authorized'])}; "
        f"authority unknown: {', '.join(p['offers']['authority_unknown'])}",
        f"- prohibited consequence categories: {p['consequences']['prohibited_categories']}",
        f"- calling window: {p['calling_window']['start']}-{p['calling_window']['end']} {p['calling_window']['timezone']}",
        f"- out of scope (always): {', '.join(reg.oos_codes)}",
    ]
    return "\n".join(lines)


def rubric_section_sha256(spec: Spec) -> str:
    return hashlib.sha256(rubric_section(spec).encode("utf-8")).hexdigest()


def render_transcript(ni: NormalizedInput) -> str:
    if not ni.turns:
        return "(no turns)"
    out = []
    for t in ni.turns:
        ts = f"{t.start_s:.1f}s" if t.start_s is not None else "--"
        out.append(f"[{t.turn}] {t.role.value} ({ts}): {t.text}")
    return "\n".join(out)


def capability_summary(ni: NormalizedInput, spec: Spec) -> str:
    reg = spec.registry
    prov = None if ni.unit_mode.value == "A" else ni.header.transcript_provenance
    oos = []
    for cid in reg.checks:
        app, reason = reg.mode_status(cid, ni.input_mode, has_call_start_ts=ni.header.call_start_ts is not None,
                                      has_timestamps=ni.has_timestamps, provenance=prov)
        if app.value == "OUT_OF_SCOPE":
            oos.append(f"{cid} ({reason.value if reason else 'MODE_CAPABILITY'})")
    return "OUT_OF_SCOPE in this input: " + (", ".join(oos) if oos else "none")


def render(name: str, **values: object) -> str:
    text, _ = load_prompt(name)
    return Template(text).substitute({k: str(v) for k, v in values.items()})


def prompt_hashes(names: tuple[str, ...], spec: Spec) -> dict[str, str]:
    hashes = {n: load_prompt(n)[1] for n in names}
    hashes["rubric_section"] = rubric_section_sha256(spec)
    return hashes
