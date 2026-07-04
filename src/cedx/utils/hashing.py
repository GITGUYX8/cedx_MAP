"""Deterministic hashing utilities for provenance."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> bytes:
    """Stable, deterministic JSON serialization."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha(obj: Any) -> str:
    """Return sha256:<hex> of the canonical JSON of obj."""
    return "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()
