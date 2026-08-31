from __future__ import annotations

import base64
from datetime import UTC, datetime

from runsigil_evidence import EvidenceSigner, verify


def test_evidence_signature_detects_content_modification() -> None:
    signer = EvidenceSigner(base64.b64encode(b"k" * 32).decode(), "test-key")
    envelope = signer.sign({"run_id": "run-1", "outcome": "committed"})
    trusted = {"test-key": envelope.public_key_b64}
    assert verify(envelope, trusted_public_keys=trusted).valid

    tampered = envelope.model_dump()
    tampered["manifest"]["outcome"] = "failed"
    result = verify(tampered, trusted_public_keys=trusted)
    assert not result.valid
    assert not result.content_digest_valid


def test_untrusted_signing_key_fails_verification() -> None:
    signer = EvidenceSigner(base64.b64encode(b"k" * 32).decode(), "test-key")
    envelope = signer.sign({"run_id": "run-1"})
    result = verify(envelope, trusted_public_keys={"other": envelope.public_key_b64})
    assert not result.valid
    assert not result.trusted_key


def test_evidence_manifest_is_json_native() -> None:
    signer = EvidenceSigner(base64.b64encode(b"k" * 32).decode(), "test-key")
    envelope = signer.sign({"completed_at": datetime(2026, 8, 31, tzinfo=UTC)})
    assert envelope.manifest["completed_at"] == "2026-08-31T00:00:00+00:00"
