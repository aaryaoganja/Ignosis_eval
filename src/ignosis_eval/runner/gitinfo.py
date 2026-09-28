"""Git provenance of the evaluation code (recorded in every run manifest)."""

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
    """Commit, branch and dirty flag of the repository containing this package. Fails closed."""
    commit = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    dirty = bool(_git("status", "--porcelain"))  # untracked (non-ignored) files also count as dirty
    return GitInfo(commit=commit, branch=branch, dirty=dirty)
