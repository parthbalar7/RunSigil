from __future__ import annotations

import base64
import hashlib
from typing import Any, Literal, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel, ConfigDict
from runsigil_contracts.canonical import canonical_digest, canonical_json_value

_DOMAIN = b"runsigil:evidence-bundle:v1\0"


class EvidenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    manifest: dict[str, Any]
    content_digest: str
    signature_algorithm: Literal["ed25519"] = "ed25519"
    signing_key_id: str
    public_key_b64: str
    signature_b64: str


class VerificationResult(BaseModel):
    valid: bool
    content_digest_valid: bool
    signature_valid: bool
    trusted_key: bool
    message: str


class EvidenceSigner:
    def __init__(self, private_key_b64: str, key_id: str) -> None:
        raw = base64.b64decode(private_key_b64, validate=True)
        if len(raw) != 32:
            raise ValueError("Ed25519 private key seed must contain exactly 32 bytes")
        if not key_id.strip():
            raise ValueError("evidence signing key id is required")
        self._private_key = Ed25519PrivateKey.from_private_bytes(raw)
        self._key_id = key_id.strip()

    def sign(self, manifest: dict[str, Any]) -> EvidenceEnvelope:
        normalized_manifest = cast(dict[str, Any], canonical_json_value(manifest))
        digest = canonical_digest(normalized_manifest)
        signature = self._private_key.sign(_DOMAIN + digest.encode("ascii"))
        public_key = self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return EvidenceEnvelope(
            manifest=normalized_manifest,
            content_digest=digest,
            signing_key_id=self._key_id,
            public_key_b64=base64.b64encode(public_key).decode("ascii"),
            signature_b64=base64.b64encode(signature).decode("ascii"),
        )


def verify(
    envelope: EvidenceEnvelope | dict[str, Any],
    *,
    trusted_public_keys: dict[str, str] | None = None,
) -> VerificationResult:
    try:
        parsed = (
            envelope
            if isinstance(envelope, EvidenceEnvelope)
            else EvidenceEnvelope.model_validate(envelope)
        )
    except Exception:
        return VerificationResult(
            valid=False,
            content_digest_valid=False,
            signature_valid=False,
            trusted_key=False,
            message="Evidence envelope schema is invalid.",
        )

    calculated = canonical_digest(parsed.manifest)
    digest_valid = calculated == parsed.content_digest
    signature_valid = False
    try:
        public_raw = base64.b64decode(parsed.public_key_b64, validate=True)
        signature_raw = base64.b64decode(parsed.signature_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(public_raw).verify(
            signature_raw,
            _DOMAIN + parsed.content_digest.encode("ascii"),
        )
        signature_valid = True
    except (ValueError, InvalidSignature):
        signature_valid = False

    if trusted_public_keys is None:
        trusted = True
        trust_message = "signature is self-consistent; no external trust root was supplied"
    else:
        trusted = trusted_public_keys.get(parsed.signing_key_id) == parsed.public_key_b64
        trust_message = "signing key is trusted" if trusted else "signing key is not trusted"
    valid = digest_valid and signature_valid and trusted
    message = (
        f"Evidence is valid ({trust_message})."
        if valid
        else "Evidence verification failed: content, signature, or trust root mismatch."
    )
    return VerificationResult(
        valid=valid,
        content_digest_valid=digest_valid,
        signature_valid=signature_valid,
        trusted_key=trusted,
        message=message,
    )


def public_key_fingerprint(public_key_b64: str) -> str:
    return "sha256:" + hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()
