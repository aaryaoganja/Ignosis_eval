"""On-disk benchmark layout.

    <root>/
      dataset/<split>/<case_id>/input.json        Canonical Input (the ONLY evaluator-visible part)
      dataset/<split>/<case_id>/<audio + sidecars> audio referenced by input.json (optional)
      case_cards/<split>/<case_id>.card.yaml       author intent (protected from evaluator)
      gold/<split>/<case_id>.gold.json             gold labels (protected, frozen)
      manifests/benchmark_manifest.json            dataset manifest + hashes (protected)
      manifests/gold_manifest.json                 gold manifest + hashes (protected)
      manifests/holdout_registry.json              append-only list of holdout case ids (protected)
      templates/case_card.template.yaml            authoring template

`<split>` is one of dev | holdout | redteam | calibration. The split is encoded twice (directory and
case card / gold `split` field) so that a case filed or marked in the wrong split is detected.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ignosis_eval.canonical import canonical_sha256
from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.enums import Split
from ignosis_eval.contracts.evidence import normalize_text

INPUT_FILENAME = "input.json"
CARD_SUFFIX = ".card.yaml"
GOLD_SUFFIX = ".gold.json"


@dataclass(frozen=True)
class CasePaths:
    split: Split
    case_id: str
    case_dir: Path
    input_path: Path
    card_path: Path
    gold_path: Path


class BenchmarkLayout:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.dataset_dir = self.root / "dataset"
        self.gold_dir = self.root / "gold"
        self.cards_dir = self.root / "case_cards"
        self.manifests_dir = self.root / "manifests"
        self.templates_dir = self.root / "templates"
        self.benchmark_manifest_path = self.manifests_dir / "benchmark_manifest.json"
        self.gold_manifest_path = self.manifests_dir / "gold_manifest.json"
        self.holdout_registry_path = self.manifests_dir / "holdout_registry.json"

    @property
    def protected_paths(self) -> list[Path]:
        """Paths the evaluator process must never read or write."""
        return [self.gold_dir, self.cards_dir, self.manifests_dir]

    def case_paths(self, split: Split, case_id: str) -> CasePaths:
        case_dir = self.dataset_dir / split.value / case_id
        return CasePaths(
            split=split,
            case_id=case_id,
            case_dir=case_dir,
            input_path=case_dir / INPUT_FILENAME,
            card_path=self.cards_dir / split.value / f"{case_id}{CARD_SUFFIX}",
            gold_path=self.gold_dir / split.value / f"{case_id}{GOLD_SUFFIX}",
        )

    def rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    def abs(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"path escapes benchmark root: {rel}")
        return p

    # ---------------------------------------------------------------------------------- discovery
    def discover_dataset(self) -> tuple[list[CasePaths], list[str]]:
        """Return case paths found under dataset/ and a list of layout problems."""
        cases: list[CasePaths] = []
        problems: list[str] = []
        if not self.dataset_dir.exists():
            return cases, [f"missing dataset directory {self.dataset_dir}"]
        valid = {s.value for s in Split}
        for split_dir in sorted(p for p in self.dataset_dir.iterdir() if not p.name.startswith(".")):
            if not split_dir.is_dir() or split_dir.name not in valid:
                problems.append(f"unexpected entry in dataset/: {split_dir.name}")
                continue
            for case_dir in sorted(p for p in split_dir.iterdir() if not p.name.startswith(".")):
                if not case_dir.is_dir():
                    problems.append(f"unexpected file in dataset/{split_dir.name}/: {case_dir.name}")
                    continue
                cases.append(self.case_paths(Split(split_dir.name), case_dir.name))
        return cases, problems

    def _discover_suffixed(self, base: Path, suffix: str) -> tuple[list[tuple[str, str, Path]], list[str]]:
        found: list[tuple[str, str, Path]] = []
        problems: list[str] = []
        if not base.exists():
            return found, problems
        valid = {s.value for s in Split}
        for split_dir in sorted(p for p in base.iterdir() if not p.name.startswith(".")):
            if not split_dir.is_dir() or split_dir.name not in valid:
                problems.append(f"unexpected entry in {base.name}/: {split_dir.name}")
                continue
            for f in sorted(p for p in split_dir.iterdir() if not p.name.startswith(".")):
                if not f.name.endswith(suffix):
                    problems.append(f"unexpected file {base.name}/{split_dir.name}/{f.name}")
                    continue
                found.append((split_dir.name, f.name[: -len(suffix)], f))
        return found, problems

    def discover_gold(self) -> tuple[list[tuple[str, str, Path]], list[str]]:
        return self._discover_suffixed(self.gold_dir, GOLD_SUFFIX)

    def discover_cards(self) -> tuple[list[tuple[str, str, Path]], list[str]]:
        return self._discover_suffixed(self.cards_dir, CARD_SUFFIX)


def content_fingerprint(inp: CanonicalInput) -> str:
    """Hash of the modality content only (ignores call_id and metadata) — detects cross-split leakage."""
    turns = []
    if inp.transcript is not None:
        turns = [[t.speaker.value, normalize_text(t.text)] for t in inp.transcript.turns]
    return canonical_sha256({"audio_sha256": inp.audio.sha256 if inp.audio else None, "turns": turns})
