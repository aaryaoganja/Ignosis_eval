"""Holdout lock — experiment-protocol P-8 and hard requirement H7.

The runner refuses a locked (holdout / red-team) run unless, before the run_id exists:
  1. the evaluator commit is tagged and the working tree is clean (P-8 step 1);
  2. the manifest will be written with locked: true (P-8 step 2);
  3. the hashes of private data (the private hash list), gold, rubric, profile and lexicons verify (step 3);
plus the preconditions the frozen documents attach to locked runs: the operator confirmed the holdout run
(--confirm-holdout), no blocking PENDING_HUMAN_SIGNOFF item remains, the spec-pack profile is used, a real
LLM backend is configured (not the replay mock), one locked run per frozen evaluator version (§13 rule 3),
and the red-team run follows the holdout run (P-9).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ignosis_eval.benchmark.holdout import LockedRunError, check_locked_run_allowed
from ignosis_eval.benchmark.layout import BenchLayout
from ignosis_eval.contracts.run_manifest import GitInfo, HashVerification, PendingRef, SystemConfig
from ignosis_eval.integrity.freeze import IntegrityError, verify_bench, verify_gold
from ignosis_eval.runner.storage import ResultsLayout
from ignosis_eval.spec.loader import Spec

LOCKED_KINDS = ("locked_holdout", "locked_redteam")


class LockError(RuntimeError):
    pass


def verify_hashes(layout: BenchLayout, scope: str, spec: Spec) -> HashVerification:
    """P-8 step 3. Raises IntegrityError on any drift (fail closed)."""
    _, bsha = verify_bench(layout, scope)
    verify_gold(layout, scope, expected_bench_manifest_sha256=bsha)
    for name, sha in spec.file_sha256.items():
        if not sha:
            raise IntegrityError(f"spec file {name} unhashed")
    return HashVerification(verified_at=datetime.now(timezone.utc), bench_manifest=True, item_files=True,
                            private_hash_list=True if scope == "private" else None, gold=True, rubric=True,
                            profile=True, lexicons=bool(spec.lexicons_sha256))


def preflight(kind: str, *, split: str, confirm_holdout: bool, git: GitInfo, spec: Spec, pending: list[PendingRef],
              systems: list[SystemConfig], results: ResultsLayout) -> None:
    if kind not in LOCKED_KINDS:
        if split != "dev":
            raise LockError("holdout and red-team data can only be run by a locked run (P-1, P-8)")
        return
    problems: list[str] = []
    expected_split = "holdout" if kind == "locked_holdout" else "redteam"
    if split != expected_split:
        problems.append(f"{kind} runs the {expected_split} split, not {split}")
    if not confirm_holdout:
        problems.append("locked runs require --confirm-holdout (the holdout is never used for tuning)")
    if not git.tag or git.dirty:
        problems.append("tag the evaluator commit and run from a clean working tree (P-8 step 1)")
    blocking = [p.path for p in pending if p.blocks_locked_run]
    if blocking:
        problems.append(f"blocking PENDING_HUMAN_SIGNOFF items unresolved: {blocking}")
    if not spec.is_canonical_profile:
        problems.append("locked runs must use the spec-pack profile (docs/spec/profile.yaml)")
    if any(s.llm_backend == "mock_replay" for s in systems):
        problems.append("locked runs cannot use the mock replay backend")
    if git.tag:
        try:
            check_locked_run_allowed(results.locked_runs, kind, git.tag)
        except LockedRunError as exc:
            problems.append(str(exc))
    if problems:
        raise LockError("locked run refused (H7 / P-8):\n  " + "\n  ".join(problems))
