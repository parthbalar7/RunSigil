from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from runsigil_contracts.canonical import canonical_bytes


def decode_aes256_key(value: str) -> bytes:
    try:
        key = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError("action encryption key must be valid base64") from exc
    if len(key) != 32:
        raise ValueError("action encryption key must contain exactly 32 bytes")
    return key


def seal_json(value: Any, *, key: bytes, associated_data: dict[str, Any]) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(
        nonce, canonical_bytes(value), canonical_bytes(associated_data)
    )
    return "rsenc1:" + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def open_json(value: str, *, key: bytes, associated_data: dict[str, Any]) -> Any:
    if not value.startswith("rsenc1:"):
        raise ValueError("unsupported encrypted payload format")
    raw = base64.urlsafe_b64decode(value.removeprefix("rsenc1:").encode("ascii"))
    if len(raw) < 29:
        raise ValueError("encrypted payload is truncated")
    plaintext = AESGCM(key).decrypt(raw[:12], raw[12:], canonical_bytes(associated_data))
    return json.loads(plaintext.decode("utf-8"))
