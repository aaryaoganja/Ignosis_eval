"""System interface shared by K0, A, A+ and B (frozen-contract §11).

A system sees only the NormalizedInput and an EvaluationContext. It never receives item ids, case metadata,
case cards, manifests, registries or gold, and it runs inside the gold-access guard.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ignosis_eval.canonical import canonical_json_bytes
from ignosis_eval.contracts.canonical_input import NormalizedInput
from ignosis_eval.contracts.enums import System
from ignosis_eval.contracts.evaluation_record import EvaluationRecord, SystemInfo
from ignosis_eval.contracts.run_manifest import SystemConfig
from ignosis_eval.spec.loader import Spec


def _usage() -> dict[str, int]:
    return {"llm_calls": 0, "transport_retries": 0, "schema_retries": 0, "input_tokens": 0, "output_tokens": 0,
            "cached_tokens": 0}


@dataclass
class TraceSink:
    """Raw LLM traffic and usage for one (system, unit, rep); persisted by the runner (P-11)."""

    requests: list[dict] = field(default_factory=list)
    responses: list[dict] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=_usage)
    derivation: list[dict] = field(default_factory=list)  # B's derivation log (verifier, rules, judgments)


@dataclass
class EvaluationContext:
    repetition: int
    spec: Spec
    trace: TraceSink = field(default_factory=TraceSink)


def input_sha256(ni: NormalizedInput) -> str:
    """Hash of the evaluated content. The per-run random `unit_alias` (P-17) is excluded, so a replayed LLM response
    and the P-6 audit key on what was evaluated, not on the run."""
    body = {k: v for k, v in ni.to_json_dict().items() if k != "unit_alias"}
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


class Evaluator(ABC):
    system: System
    version: str

    @abstractmethod
    def config(self) -> SystemConfig: ...

    @abstractmethod
    def evaluate(self, ni: NormalizedInput, ctx: EvaluationContext) -> EvaluationRecord: ...

    def system_info(self) -> SystemInfo:
        c = self.config()
        return SystemInfo(system=c.system.value, version=c.version, model_snapshot_id=c.model_snapshot_id,
                          prompt_hashes=dict(c.prompt_hashes))
