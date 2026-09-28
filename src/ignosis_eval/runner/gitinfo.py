"""Git provenance of the evaluation code (recorded in every run manifest; P-8 step 1 needs the tag)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ignosis_eval.contracts.run_manifest import GitInfo

PACKAGE_DIR = Path(__file__).resolve().parents[1]


class GitInfoError(RuntimeError):
    pass


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(PACKAGE_DIR), *args], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GitInfoError(f"git {' '.join(args)} failed: {exc}") from exc
    return out.stdout.strip()


def git_info() -> GitInfo:
    """Commit, branch, dirty flag and a tag pointing at HEAD (if any). Fails closed if git is unavailable."""
    commit = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    dirty = bool(_git("status", "--porcelain"))  # untracked (non-ignored) files also count as dirty
    tags = sorted(t for t in _git("tag", "--points-at", "HEAD").splitlines() if t.strip())
    return GitInfo(commit=commit, branch=branch, dirty=dirty, tag=tags[0] if tags else None)
