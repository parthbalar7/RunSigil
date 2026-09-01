from __future__ import annotations

import pytest
from runsigil_gateway.egress import validate_fixed_destination
from runsigil_gateway.tokens import (
    mint_audience_token,
    mint_model_audience_token,
    verify_audience_token,
)


def test_audience_token_is_bound_and_short_lived() -> None:
    token = mint_audience_token(
        signing_key="provider-signing-key-with-32-characters",
        audience="provider-a",
        subject="runsigil:workload:agent-a",
        action_id="action-a",
        run_id="run-a",
        content_digest="sha256:" + "a" * 64,
    )
    claims = verify_audience_token(
        token,
        signing_key="provider-signing-key-with-32-characters",
        audience="provider-a",
    )
    assert claims["sub"] == "runsigil:workload:agent-a"
    assert claims["aud"] == "provider-a"
    assert claims["exp"] - claims["iat"] == 60
    with pytest.raises(ValueError, match="audience"):
        verify_audience_token(
            token,
            signing_key="provider-signing-key-with-32-characters",
            audience="provider-b",
        )


def test_model_audience_token_is_bound_without_an_action_claim() -> None:
    token = mint_model_audience_token(
        signing_key="provider-signing-key-with-32-characters",
        audience="provider-a",
        subject="runsigil:workload:agent-a",
        model_call_id="model-call-a",
        run_id="run-a",
        content_digest="sha256:" + "a" * 64,
    )
    claims = verify_audience_token(
        token,
        signing_key="provider-signing-key-with-32-characters",
        audience="provider-a",
    )

    assert claims["model_call_id"] == "model-call-a"
    assert "action_id" not in claims


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:password@example.com/effects",
        "https://example.com/effects?token=secret",
        "https://example.com/effects#fragment",
    ],
)
def test_egress_rejects_unsafe_fixed_destinations(url: str) -> None:
    with pytest.raises(ValueError):
        validate_fixed_destination(url, allow_private=False, production=True)


def test_private_destination_requires_explicit_development_override() -> None:
    with pytest.raises(ValueError, match="non-global"):
        validate_fixed_destination(
            "http://127.0.0.1:8090/effects", allow_private=False, production=False
        )
    validate_fixed_destination(
        "http://127.0.0.1:8090/effects", allow_private=True, production=False
    )
