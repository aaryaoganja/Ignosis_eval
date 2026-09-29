"""Prompt assembly: hand-written stub instruction templates + a GENERATED rubric section.

experiment-protocol P-2: "The rubric text inside prompts is generated from rubric.yaml + profile.yaml by a
versioned template. Hand-written instruction text around the generated section is the tunable part."
Instruction templates are the files in evaluators/prompts/ (all marked UNOPTIMIZED STUB; not tuned).
A+ has no prompt of its own (§11).

Template 0.4.0 adds two GENERATED sections, so that a model's output can be parsed at all (functional contract
completion, not tuning): `$output_schema` (the JSON Schema of the contract the parser validates: RecordBody for A,
ExtractionOutput for B's extraction, JudgmentOutput for B's judgments) and `$extraction_guide` (rubric.yaml ›
extraction_vocabulary and extraction_schema, verbatim, plus the call_frame / call_end shapes). Their hashes are part
of `prompt_hashes`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from string import Template

import yaml

from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.evaluation_record import RecordBody
from ignosis_eval.contracts.extraction import ExtractionOutput
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


def output_schema(name: str) -> str:
    """The JSON Schema of the contract a prompt's answer is parsed into (generated from the contracts)."""
    if name == "a_holistic":
        schema = RecordBody.model_json_schema()
    elif name == "b_extraction":
        schema = ExtractionOutput.model_json_schema()
    elif name == "b_judgments":
        from ignosis_eval.evaluators.judgement import JudgmentOutput

        schema = JudgmentOutput.model_json_schema()
    else:
        raise KeyError(f"no output contract for prompt {name!r}")
    return json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def extraction_guide(spec: Spec) -> str:
    """rubric.yaml › extraction_vocabulary + extraction_schema, verbatim, and the two untyped extraction shapes."""
    body = {"extraction_vocabulary": spec.rubric["extraction_vocabulary"],
            "extraction_schema": spec.rubric["extraction_schema"]}
    return (yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=120).rstrip() + "\n"
            "call_frame.agent_org_statement: {\"turn\": <agent turn>, \"quote\": \"<verbatim>\"} or null\n"
            "call_end: {\"ended_by\": \"agent\" | \"borrower\" | \"unknown\", "
            "\"pending_borrower_question_turn\": <turn> | null, \"next_step_quote\": \"<verbatim>\" | null}\n"
            "turn_languages: {\"<turn>\": \"en\" | \"hi\" | \"hi-en\" | \"other\"} for every turn")


def render(name: str, **values: object) -> str:
    text, _ = load_prompt(name)
    return Template(text).substitute({k: str(v) for k, v in values.items()})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_hashes(names: tuple[str, ...], spec: Spec) -> dict[str, str]:
    hashes = {n: load_prompt(n)[1] for n in names}
    hashes["rubric_section"] = rubric_section_sha256(spec)
    for n in names:
        hashes[f"output_schema:{n}"] = _sha(output_schema(n))
    if "b_extraction" in names:
        hashes["extraction_guide"] = _sha(extraction_guide(spec))
    return hashes
