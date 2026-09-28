"""ASR interface for audio-only inputs.

`MockASR` stands in for a real ASR system: it returns the turns stored in a TEST sidecar file
`<audio uri>.mock_asr.json` next to the audio, and fails (returns None) when there is none — which is
how an unintelligible recording is simulated. A real ASR client implements the same interface and its
engine/model/version are recorded in the run manifest.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ignosis_eval.contracts.canonical_input import AudioMetadata, Turn


class ASRClient(ABC):
    engine: str
    model: str | None
    version: str

    @abstractmethod
    def transcribe(self, audio: AudioMetadata, audio_path: Path) -> list[Turn] | None:
        """Return diarized, timestamped turns, or None if nothing intelligible was recognized."""

    def describe(self) -> dict[str, Any]:
        return {"engine": self.engine, "model": self.model, "version": self.version, "config": {}}


class MockASR(ASRClient):
    engine, model, version = "mock-asr", None, "0"

    def transcribe(self, audio: AudioMetadata, audio_path: Path) -> list[Turn] | None:
        sidecar = audio_path.with_name(audio_path.name + ".mock_asr.json")
        if not sidecar.exists():
            return None
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return [Turn.model_validate(t) for t in data["turns"]]
