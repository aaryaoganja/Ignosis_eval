"""File and tree hashing.

* `.json` files are hashed in canonical form (semantic hash): re-indenting or re-ordering keys does not
  change the hash; any change of content does.
* every other file (audio, YAML case cards, sidecars) is hashed as raw bytes.
* a tree hash is the SHA-256 of the sorted "relative_path<TAB>sha256" lines.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from ignosis_eval.canonical import canonical_json_bytes, canonical_json_pretty, sha256_bytes
from ignosis_eval.contracts.benchmark import FileHash


def hash_file(path: Path, rel: str) -> FileHash:
    data = path.read_bytes()
    if path.suffix == ".json":
        obj = json.loads(data.decode("utf-8"))
        return FileHash(path=rel, sha256=sha256_bytes(canonical_json_bytes(obj)), kind="json_canonical")
    return FileHash(path=rel, sha256=sha256_bytes(data), kind="raw_bytes")


def tree_hash(entries: Iterable[tuple[str, str]]) -> str:
    lines = sorted(f"{rel}\t{sha}" for rel, sha in entries)
    return sha256_bytes("\n".join(lines).encode("utf-8"))


def file_canonical_sha256(path: Path) -> str:
    return sha256_bytes(canonical_json_bytes(json.loads(path.read_text(encoding="utf-8"))))


def canonicalize_json_file(path: Path) -> bool:
    """Rewrite a JSON file in canonical pretty form. Returns True if the bytes changed."""
    raw = path.read_text(encoding="utf-8")
    pretty = canonical_json_pretty(json.loads(raw))
    if pretty != raw:
        path.write_text(pretty, encoding="utf-8")
        return True
    return False
