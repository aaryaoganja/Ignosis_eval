"""Prompt template loading, hashing and rendering (string.Template: `$name` placeholders)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from string import Template

from ignosis_eval.contracts.canonical_input import CanonicalInput

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str) -> tuple[str, str]:
    data = (PROMPT_DIR / f"{name}.md").read_bytes()
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest()


def prompt_hashes(names: tuple[str, ...]) -> dict[str, str]:
    return {n: load_prompt(n)[1] for n in names}


def render_transcript(inp: CanonicalInput) -> str:
    if inp.transcript is None:
        return "(no transcript available)"
    lines = []
    for t in inp.transcript.turns:
        ts = f"{t.start_ms / 1000:.1f}s" if t.start_ms is not None else "--"
        lines.append(f"[{t.turn_id}] {t.speaker.value.upper()} ({ts}): {t.text}")
    return "\n".join(lines)


def render(name: str, **values: object) -> str:
    text, _ = load_prompt(name)
    return Template(text).substitute({k: str(v) for k, v in values.items()})
