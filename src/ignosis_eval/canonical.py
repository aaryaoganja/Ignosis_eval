"""Canonical JSON serialization and hashing primitives (dependency-free foundation).

Canonical form: Unicode NFC-normalized strings, object keys sorted, no insignificant whitespace,
UTF-8, no NaN/Infinity. Semantically identical JSON documents therefore hash identically regardless of
key order, indentation or Unicode composition; any semantic change changes the hash.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from typing import Any


def canonicalize(obj: Any) -> Any:
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, bool) or obj is None or isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("NaN/Infinity are not allowed in canonical JSON")
        return obj
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"canonical JSON keys must be strings, got {type(k).__name__}")
            nk = unicodedata.normalize("NFC", k)
            if nk in out:
                raise ValueError(f"duplicate key after NFC normalization: {nk!r}")
            out[nk] = canonicalize(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [canonicalize(v) for v in obj]
    raise TypeError(f"type {type(obj).__name__} is not JSON-serializable in canonical form")


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(
        canonicalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def canonical_json_pretty(obj: Any) -> str:
    """Human-readable canonical rendering used when (re)writing files to disk."""
    return json.dumps(canonicalize(obj), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(obj: Any) -> str:
    return sha256_bytes(canonical_json_bytes(obj))
