from __future__ import annotations

from datetime import UTC, datetime

import pytest
from runsigil_contracts import canonical_bytes, canonical_digest, canonical_json_value


def test_canonical_json_is_order_independent() -> None:
    left = {"b": [2, 1], "a": {"value": True}}
    right = {"a": {"value": True}, "b": [2, 1]}
    assert canonical_bytes(left) == canonical_bytes(right)
    assert canonical_digest(left) == canonical_digest(right)


def test_canonical_json_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        canonical_bytes({"cost": float("nan")})


def test_canonical_json_value_normalizes_database_json_values() -> None:
    timestamp = datetime(2026, 8, 31, 12, 30, tzinfo=UTC)
    assert canonical_json_value({"recorded_at": timestamp}) == {
        "recorded_at": "2026-08-31T12:30:00+00:00"
    }
