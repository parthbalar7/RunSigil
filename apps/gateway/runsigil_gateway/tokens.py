from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, cast

from runsigil_contracts.canonical import canonical_bytes


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def mint_audience_token(
    *,
    signing_key: str,
    audience: str,
    subject: str,
    action_id: str,
    run_id: str,
    content_digest: str,
    lifetime_seconds: int = 60,
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT", "kid": "runsigil-demo-provider-v1"}
    claims = {
        "iss": "runsigil-gateway",
        "aud": audience,
        "sub": subject,
        "iat": now,
        "exp": now + lifetime_seconds,
        "jti": secrets.token_urlsafe(16),
        "action_id": action_id,
        "run_id": run_id,
        "content_digest": content_digest,
    }
    encoded = f"{_b64url(canonical_bytes(header))}.{_b64url(canonical_bytes(claims))}"
    signature = hmac.new(
        signing_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded}.{_b64url(signature)}"


def verify_audience_token(token: str, *, signing_key: str, audience: str) -> dict[str, Any]:
    header_part, claims_part, signature_part = token.split(".", 2)
    signed = f"{header_part}.{claims_part}"
    expected = hmac.new(
        signing_key.encode("utf-8"), signed.encode("ascii"), hashlib.sha256
    ).digest()
    supplied = _decode_b64url(signature_part)
    if not hmac.compare_digest(expected, supplied):
        raise ValueError("token signature is invalid")
    raw_header: Any = json.loads(_decode_b64url(header_part))
    raw_claims: Any = json.loads(_decode_b64url(claims_part))
    if not isinstance(raw_header, dict) or not isinstance(raw_claims, dict):
        raise ValueError("token header and claims must be JSON objects")
    header = cast(dict[str, Any], raw_header)
    claims = cast(dict[str, Any], raw_claims)
    if header.get("alg") != "HS256" or claims.get("iss") != "runsigil-gateway":
        raise ValueError("token issuer or algorithm is invalid")
    if claims.get("aud") != audience or int(claims.get("exp", 0)) <= int(time.time()):
        raise ValueError("token audience or lifetime is invalid")
    return claims
