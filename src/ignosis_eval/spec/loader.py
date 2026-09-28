"""Load the frozen specification pack (docs/spec/) — the single source of rule parameters and policy values.

Rules enforced here:
  * Versions on disk must equal the versions this implementation was reconciled against; otherwise the
    loader fails closed (a changed spec needs a new reconciliation, not silent reinterpretation).
  * `PENDING_HUMAN_SIGNOFF` values are preserved. Reading one raises PendingHumanSignoffError — no
    default is ever substituted.
  * Lexicons: only `terms` are exposed. `seed_candidates_unreviewed` are never returned (B-04).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from ignosis_eval.canonical import canonical_sha256
from ignosis_eval.versions import SPEC_CONTRACT_VERSION, SPEC_PROFILE_ID, SPEC_RUBRIC_VERSION

PENDING = "PENDING_HUMAN_SIGNOFF"
SPEC_FILES = ("frozen-contract.md", "rubric.yaml", "profile.yaml", "experiment-protocol.md", "scoring-spec.md",
              "implementation-blockers.md")


class SpecError(RuntimeError):
    """The spec pack is missing, malformed, or at an unexpected version (fail closed)."""


class PendingHumanSignoffError(RuntimeError):
    """A value marked PENDING_HUMAN_SIGNOFF was needed. No default is substituted."""

    def __init__(self, path: str, decision_required: str | None = None, blocker: str | None = None):
        self.path, self.decision_required, self.blocker = path, decision_required, blocker
        msg = f"{path} is PENDING_HUMAN_SIGNOFF"
        if blocker:
            msg += f" ({blocker})"
        if decision_required:
            msg += f": {decision_required.strip()}"
        super().__init__(msg)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_spec_dir() -> Path:
    env = os.environ.get("IGNOSIS_SPEC_DIR")
    return Path(env) if env else repo_root() / "docs" / "spec"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lexicon_terms_projection(lexicons: dict[str, Any]) -> Any:
    """Keep only `terms` (and structure/metadata that affects matching); drop unreviewed seeds."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k != "seed_candidates_unreviewed"}
        return node

    return walk(lexicons)


@dataclass(frozen=True)
class Spec:
    spec_dir: Path
    profile_path: Path
    rubric: dict[str, Any]
    profile: dict[str, Any]
    rubric_sha256: str
    profile_sha256: str
    file_sha256: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ identity
    @property
    def contract_version(self) -> str:
        return str(self.rubric["contract_version"])

    @property
    def rubric_version(self) -> str:
        return str(self.rubric["rubric_version"])

    @property
    def profile_id(self) -> str:
        return str(self.profile["profile_id"])

    @property
    def profile_version(self) -> str:
        return str(self.profile["profile_version"])

    @property
    def lexicons_sha256(self) -> str:
        return canonical_sha256(_lexicon_terms_projection(self.profile.get("lexicons", {})))

    @property
    def is_canonical_profile(self) -> bool:
        """True iff the profile is the file shipped in the spec pack (required for locked runs)."""
        return self.profile_path.resolve() == (self.spec_dir / "profile.yaml").resolve()

    # ------------------------------------------------------------------ values
    def threshold(self, name: str) -> Any:
        node = self.profile["thresholds"][name]
        value = node["value"]
        if value == PENDING:
            from ignosis_eval.spec.pending import blocker_for

            raise PendingHumanSignoffError(f"profile.thresholds.{name}", node.get("decision_required"),
                                           blocker_for(f"profile.thresholds.{name}.value"))
        return value

    def lexicon_terms(self, *path: str) -> Any:
        """Return `terms` of a lexicon node, e.g. lexicon_terms('prohibited_consequences', 'arrest')."""
        node: Any = self.profile["lexicons"]
        for key in path:
            node = node[key]
        if not isinstance(node, dict) or "terms" not in node:
            raise SpecError(f"lexicons.{'.'.join(path)} has no `terms`")
        return node["terms"]

    def lexicon_review_pending(self) -> bool:
        return self.profile["lexicons"].get("review_status") == PENDING

    @cached_property
    def registry(self):  # noqa: ANN201 - avoid import cycle in the annotation
        from ignosis_eval.spec.registry import Registry

        return Registry.from_rubric(self.rubric)

    def enum_values(self, name: str) -> list[str]:
        return list(self.rubric["enums"][name])


def load_spec(spec_dir: str | Path | None = None, *, profile_path: str | Path | None = None) -> Spec:
    sdir = Path(spec_dir) if spec_dir else default_spec_dir()
    missing = [f for f in SPEC_FILES if not (sdir / f).exists()]
    if missing:
        raise SpecError(f"spec pack incomplete in {sdir}: missing {missing}")
    ppath = Path(profile_path) if profile_path else sdir / "profile.yaml"
    try:
        rubric = yaml.safe_load((sdir / "rubric.yaml").read_text(encoding="utf-8"))
        profile = yaml.safe_load(ppath.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SpecError(f"cannot read spec YAML: {exc}") from exc
    problems = []
    if rubric.get("contract_version") != SPEC_CONTRACT_VERSION:
        problems.append(f"rubric contract_version {rubric.get('contract_version')!r} != {SPEC_CONTRACT_VERSION!r}")
    if str(rubric.get("rubric_version")) != SPEC_RUBRIC_VERSION:
        problems.append(f"rubric_version {rubric.get('rubric_version')!r} != {SPEC_RUBRIC_VERSION!r}")
    if profile.get("profile_id") != SPEC_PROFILE_ID:
        problems.append(f"profile_id {profile.get('profile_id')!r} != {SPEC_PROFILE_ID!r}")
    if str(profile.get("rubric_ref")) != str(rubric.get("rubric_version")):
        problems.append("profile.rubric_ref does not match rubric_version")
    if problems:
        raise SpecError("spec version mismatch (fail closed): " + "; ".join(problems))
    from pydantic import ValidationError

    from ignosis_eval.contracts.profile import ProfileSpec

    try:
        ProfileSpec.model_validate(profile)
    except ValidationError as exc:
        raise SpecError(f"profile.yaml does not have the frozen structure: {exc}") from exc
    hashes = {f: _sha256_file(sdir / f) for f in SPEC_FILES}
    return Spec(spec_dir=sdir, profile_path=ppath, rubric=rubric, profile=profile,
                rubric_sha256=hashes["rubric.yaml"], profile_sha256=_sha256_file(ppath), file_sha256=hashes)
