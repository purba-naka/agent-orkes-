import hashlib
import json
from typing import Any


def canonical_json_dumps(obj: Any) -> bytes:
    """Produce deterministic canonical UTF-8 JSON bytes with sorted keys and compact separators."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def compute_content_hash(obj: Any) -> str:
    """Produce SHA-256 hex digest of canonical JSON serialization."""
    return hashlib.sha256(canonical_json_dumps(obj)).hexdigest()
