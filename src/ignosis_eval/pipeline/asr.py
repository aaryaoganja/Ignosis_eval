"""ASR adapter interface with a cache keyed by audio file hash (experiment-protocol P-2).

The ASR/diarization system is PENDING (B-06). No engine is chosen here. `ReplayASR` only serves
results already present in the cache (hand-made stub fixtures in tests); a cache miss raises
ASRUnavailableError instead of inventing output. The cache key includes engine, version and parameters.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ignosis_eval.canonical import canonical_json_pretty, canonical_sha256
from ignosis_eval.contracts.enums import Role


class ASRUnavailableError(RuntimeError):
    """No ASR result available (B-06 pending, or cache miss for the replay adapter)."""


@dataclass(frozen=True)
class ASRTurn:
    role: Role
    text: str
    start_s: float | None  # None when the adapter gives no reliable timing (then the unit has no timestamps)
    end_s: float | None
    diarization_confidence: float | None  # turn-level speaker confidence (DC-01, AJ-05); None for a channel split
    word_confidences: tuple[float, ...] | None = None
    unreliable: bool = False  # the adapter itself flags the turn as not heard clearly (DC-01 span-unreliable)


@dataclass(frozen=True)
class ASRResult:
    turns: tuple[ASRTurn, ...]
    mapping_confidence: float  # DC-00 call-level speaker/channel -> agent/borrower mapping confidence (AJ-05)
    engine: str
    model: str | None
    version: str
    params_sha256: str


@dataclass
class ASRAdapter(ABC):
    engine: str
    version: str
    model: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def params_sha256(self) -> str:
        return canonical_sha256(self.params)

    def cache_key(self, audio_sha256: str) -> str:
        return f"{audio_sha256}__{self.engine}__{self.version}__{self.params_sha256[:16]}.json"

    def describe(self) -> dict[str, Any]:
        return {"engine": self.engine, "model": self.model, "version": self.version, "params": self.params}

    @abstractmethod
    def transcribe(self, audio_path: Path, audio_sha256: str) -> ASRResult: ...


def _load(path: Path, adapter: ASRAdapter) -> ASRResult:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "mapping_confidence" not in data:
        raise ASRUnavailableError(f"{path.name}: cached ASR lacks the call-level mapping_confidence (DC-00)")
    turns = tuple(ASRTurn(Role(t["role"]), t["text"], float(t["start_s"]), float(t["end_s"]),
                          None if t.get("diarization_confidence") is None else float(t["diarization_confidence"]),
                          tuple(t["word_confidences"]) if t.get("word_confidences") is not None else None)
                  for t in data["turns"])
    return ASRResult(turns, float(data["mapping_confidence"]), adapter.engine, adapter.model, adapter.version,
                     adapter.params_sha256)


@dataclass
class ReplayASR(ASRAdapter):
    """Serves cached results only. Used with hand-made stub fixtures until B-06 is decided."""

    cache_dir: Path = Path(".cache/asr")

    def transcribe(self, audio_path: Path, audio_sha256: str) -> ASRResult:
        p = Path(self.cache_dir) / self.cache_key(audio_sha256)
        if not p.exists():
            raise ASRUnavailableError(f"no cached ASR for {audio_path.name} ({p.name}); ASR engine is PENDING (B-06)")
        return _load(p, self)

    @staticmethod
    def write_cache_entry(cache_dir: Path, adapter: ASRAdapter, audio_sha256: str, turns: list[dict],
                          mapping_confidence: float) -> Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        p = cache_dir / adapter.cache_key(audio_sha256)
        p.write_text(canonical_json_pretty({"mapping_confidence": mapping_confidence, "turns": turns}), encoding="utf-8")
        return p
