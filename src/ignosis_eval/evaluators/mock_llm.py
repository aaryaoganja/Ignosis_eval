"""Mock LLM that REPLAYS recorded fixtures (implementation-blockers B-05: "a mock client that replays
recorded fixtures"). It never generates content.

Replay files: <replay_dir>/<task>/<input_sha256>.json
    {"default": [<attempt 1>, <attempt 2>], "by_rep": {"3": [...]}}
Each attempt entry is either the raw response text or {"transport_error": true} (consumed as a transport
failure before the next entry). A missing file raises ReplayMissError.
"""

from __future__ import annotations

import json
from pathlib import Path

from ignosis_eval.evaluators.llm import LLMClient, LLMRequest, LLMResponse, TransportError


class ReplayMissError(RuntimeError):
    pass


class ReplayLLMClient(LLMClient):
    backend = "mock_replay"

    def __init__(self, replay_dir: str | Path, model_id: str = "mock-replay/0"):
        self.replay_dir = Path(replay_dir)
        self.model_id = model_id
        self._cursor: dict[tuple[str, str, int, int], int] = {}

    def _entries(self, request: LLMRequest) -> list:
        p = self.replay_dir / request.task / f"{request.metadata['input_sha256']}.json"
        if not p.exists():
            raise ReplayMissError(f"no replay fixture for task {request.task} input {p.stem[:12]}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("by_rep", {}).get(str(request.metadata.get("repetition")), data["default"])

    def complete(self, request: LLMRequest, attempt: int) -> LLMResponse:
        entries = self._entries(request)
        key = (request.task, str(request.metadata.get("input_sha256")), int(request.metadata.get("repetition", 0)),
               attempt)
        idx = self._cursor.get(key, 0)
        transport_skips = [e for e in entries if isinstance(e, dict) and e.get("transport_error")]
        content_entries = [e for e in entries if not (isinstance(e, dict) and e.get("transport_error"))]
        if attempt == 1 and idx < len(transport_skips):
            self._cursor[key] = idx + 1
            raise TransportError("replayed transport error")
        if attempt - 1 >= len(content_entries):
            raise ReplayMissError(f"replay fixture has no content for schema attempt {attempt}")
        content = content_entries[attempt - 1]
        text = content if isinstance(content, str) else json.dumps(content, sort_keys=True)
        prompt_chars = sum(len(m["content"]) for m in request.messages)
        return LLMResponse(request.request_id, self.model_id, text, input_tokens=prompt_chars // 4,
                           output_tokens=len(text) // 4)
