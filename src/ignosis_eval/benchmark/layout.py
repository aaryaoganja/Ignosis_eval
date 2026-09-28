"""On-disk benchmark layout — experiment-protocol P-1.

    bench/                                    (in the repository)
      dev/items/<item_id>/item.json           ItemMeta (split, pack, unit modes, artifact paths)
      dev/items/<item_id>/<transcript>        .txt or .json (§2.3); audio .wav/.mp3/.m4a (git-ignored)
      dev/case_cards/<item_id>.card.yaml      author intent (protected from evaluators)
      dev/gold/<item_id>.gold.json            dev gold (protected)
      manifests/dev_manifest.json             hash list of dev items          (BenchManifest, scope dev)
      manifests/dev_gold_manifest.json        hash list of dev gold           (GoldManifest, scope dev)
      manifests/private_manifest.json         hash list of private items      (P-1 rule 5)
      manifests/private_gold_manifest.json    hash list of private gold       (P-1 rule 5)
      manifests/history/                      write-once archive of every manifest version
      registries.json                         control / pair / twin registries (SD-01)
      asr_cache/                              cached ASR outputs, keyed by audio hash + engine (P-2)
      templates/case_card.template.yaml
    $BENCH_PRIVATE_DIR/                       (outside the repository)
      holdout/items/<item_id>/...             holdout items
      redteam/items/<item_id>/...             red-team items
      case_cards/<item_id>.card.yaml          holdout / red-team intent cards
      gold/<item_id>.gold.json                holdout and red-team gold

The legacy `benchmark/` scaffolding of the infrastructure phase is superseded by this layout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ignosis_eval.contracts.enums import Split

ITEM_META_FILE = "item.json"
CARD_SUFFIX = ".card.yaml"
GOLD_SUFFIX = ".gold.json"
PRIVATE_ENV = "BENCH_PRIVATE_DIR"
SCOPE_SPLITS: dict[str, tuple[Split, ...]] = {"dev": (Split.DEV,), "private": (Split.HOLDOUT, Split.REDTEAM)}


class LayoutError(RuntimeError):
    pass


def scope_of(split: Split) -> str:
    return "dev" if split is Split.DEV else "private"


@dataclass(frozen=True)
class ItemPaths:
    split: Split
    item_id: str
    item_dir: Path
    meta_path: Path
    card_path: Path
    gold_path: Path


class BenchLayout:
    def __init__(self, bench_root: str | Path, private_root: str | Path | None = None):
        self.root = Path(bench_root).resolve()
        env = os.environ.get(PRIVATE_ENV)
        pr = private_root if private_root is not None else (env or None)
        self.private_root = Path(pr).resolve() if pr else None
        if self.private_root is not None and (self.private_root == self.root or self.root in self.private_root.parents):
            raise LayoutError("BENCH_PRIVATE_DIR must be outside the repository bench directory (P-1)")
        self.dev_dir = self.root / "dev"
        self.manifests_dir = self.root / "manifests"
        self.history_dir = self.manifests_dir / "history"
        self.registries_path = self.root / "registries.json"
        self.asr_cache_dir = self.root / "asr_cache"
        self.templates_dir = self.root / "templates"

    # ------------------------------------------------------------------ scope roots
    def scope_root(self, scope: str) -> Path:
        if scope == "dev":
            return self.dev_dir
        if scope == "private":
            if self.private_root is None:
                raise LayoutError(f"{PRIVATE_ENV} is not set: holdout / red-team data is unavailable (fail closed)")
            return self.private_root
        raise LayoutError(f"unknown scope {scope!r}")

    def items_dir(self, split: Split) -> Path:
        if split is Split.DEV:
            return self.dev_dir / "items"
        return self.scope_root("private") / split.value / "items"

    def gold_dir(self, scope: str) -> Path:
        return self.scope_root(scope) / "gold"

    def cards_dir(self, scope: str) -> Path:
        return self.scope_root(scope) / "case_cards"

    def manifest_path(self, scope: str) -> Path:
        return self.manifests_dir / f"{scope}_manifest.json"

    def gold_manifest_path(self, scope: str) -> Path:
        return self.manifests_dir / f"{scope}_gold_manifest.json"

    def item_paths(self, split: Split, item_id: str) -> ItemPaths:
        scope = scope_of(split)
        d = self.items_dir(split) / item_id
        return ItemPaths(split, item_id, d, d / ITEM_META_FILE, self.cards_dir(scope) / f"{item_id}{CARD_SUFFIX}",
                         self.gold_dir(scope) / f"{item_id}{GOLD_SUFFIX}")

    def rel(self, scope: str, path: Path) -> str:
        return Path(path).resolve().relative_to(self.scope_root(scope)).as_posix()

    def abs(self, scope: str, rel: str) -> Path:
        root = self.scope_root(scope)
        p = (root / rel).resolve()
        if root not in p.parents:
            raise LayoutError(f"path escapes the {scope} root: {rel}")
        return p

    # ------------------------------------------------------------------ protection
    def protected_paths(self) -> list[Path]:
        """Paths no evaluator may read or write (integrity/guard.py): gold, case cards, manifests, registries."""
        out = [self.dev_dir / "gold", self.dev_dir / "case_cards", self.manifests_dir, self.registries_path]
        if self.private_root is not None:
            out += [self.private_root / "gold", self.private_root / "case_cards"]
        return out

    # ------------------------------------------------------------------ discovery
    def discover(self, split: Split) -> tuple[list[ItemPaths], list[str]]:
        base = self.items_dir(split)
        if not base.exists():
            return [], []
        items, problems = [], []
        for d in sorted(p for p in base.iterdir() if not p.name.startswith(".")):
            if not d.is_dir():
                problems.append(f"unexpected file in {split.value}/items: {d.name}")
                continue
            items.append(self.item_paths(split, d.name))
        return items, problems

    def discover_suffixed(self, base: Path, suffix: str) -> tuple[list[tuple[str, Path]], list[str]]:
        if not base.exists():
            return [], []
        found, problems = [], []
        for f in sorted(p for p in base.iterdir() if not p.name.startswith(".")):
            if not f.name.endswith(suffix):
                problems.append(f"unexpected file {base.name}/{f.name}")
                continue
            found.append((f.name[: -len(suffix)], f))
        return found, problems
