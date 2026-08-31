from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("canonical JSON rejects non-finite numbers")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical JSON rejects non-finite decimals")
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _json_value(value.value)
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_value(value: Any) -> Any:
    """Return the JSON-native value used by the canonical encoder."""

    return _json_value(value)


def canonical_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON for content binding.

    This slice uses the interoperable subset needed by its typed contracts: sorted
    string keys, no duplicate parser keys, JSON-native values, ISO timestamps, and
    finite numbers. It intentionally avoids claiming full RFC 8785 number rendering.
    """

    normalized = canonical_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()
