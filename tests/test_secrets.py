"""Secret hygiene: the Gemini key exists only as the runtime environment variable GEMINI_API_KEY. It is never
committed, never needed at build time, never sent to the browser (tests/test_app.py) and never logged."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GOOGLE_KEY = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
ASSIGNED = re.compile(r"GEMINI_API_KEY\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{12,})")


def _tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, check=True, capture_output=True).stdout
    return [REPO / p for p in out.decode("utf-8").split("\0") if p]


def _text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def test_no_key_material_in_tracked_files():
    offenders = []
    for p in _tracked():
        text = _text(p)
        if text is None:
            continue
        if GOOGLE_KEY.search(text):
            offenders.append(f"{p.relative_to(REPO)}: Google API key pattern")
        for m in ASSIGNED.finditer(text):
            offenders.append(f"{p.relative_to(REPO)}: GEMINI_API_KEY assigned a value ({m.group(1)[:4]}...)")
    assert not offenders, offenders


def test_runtime_key_value_is_not_in_the_repository():
    value = os.environ.get("GEMINI_API_KEY", "").strip()
    if not value:
        pytest.skip("GEMINI_API_KEY is not set in this environment")
    assert not [str(p) for p in _tracked() if (t := _text(p)) is not None and value in t]


def test_env_files_are_ignored_and_the_example_is_empty():
    assert (REPO / ".env.example").read_text(encoding="utf-8") == "GEMINI_API_KEY=\n"
    probe = [".env", ".env.local", ".env.production", "config/.env", ".envrc"]
    out = subprocess.run(["git", "check-ignore", *probe], cwd=REPO, capture_output=True, text=True).stdout.split()
    assert sorted(out) == sorted(probe)
    assert subprocess.run(["git", "check-ignore", ".env.example"], cwd=REPO, capture_output=True).returncode == 1
    tracked = {p.name for p in _tracked()}
    assert not {n for n in tracked if n == ".env" or (n.startswith(".env.") and n != ".env.example")}


def test_no_build_time_secret():
    docker = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(ARG|ENV)\b.*GEMINI", docker, re.MULTILINE)
    assert "ARG " not in docker  # Railway passes a service variable into a build only through a matching ARG
    ignore = (REPO / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".env" in ignore and ".env.*" in ignore and ".git" in ignore


def test_key_is_read_only_by_the_provider_config():
    readers = [p for p in (REPO / "src").rglob("*.py")
               if "GEMINI_API_KEY" in p.read_text(encoding="utf-8") and p.name not in ("provider_config.py",)]
    code_reads = [p for p in readers if re.search(r"environ(\.get)?\(?\[?[\"']GEMINI_API_KEY", p.read_text("utf-8"))]
    assert not code_reads, code_reads
