from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from runsigil_contracts import DecisionEffect, PolicyContext
from runsigil_contracts.errors import ErrorCode
from runsigil_policy import PolicyEvaluationError, evaluate


def context() -> PolicyContext:
    return PolicyContext(
        action_type="demo.invoice.send",
        resource="tool:demo.invoice.send",
        environment="production",
        risk="high",
        data_classification="confidential",
        actor_type="user",
        amount_minor=4200,
        occurred_at=datetime.now(UTC),
    )


def bundle() -> dict:
    return {
        "schema_version": 1,
        "enabled": True,
        "valid_until": None,
        "default_effect": "deny",
        "rules": [
            {
                "id": "approval-required",
                "action_type": "demo.invoice.send",
                "environments": ["production"],
                "risks": ["high"],
                "maximum_amount_minor": 10_000,
                "effect": "require_approval",
                "reason": "Human review is required.",
            }
        ],
    }


def test_policy_requires_approval() -> None:
    decision = evaluate(bundle(), context())
    assert decision.effect == DecisionEffect.REQUIRE_APPROVAL
    assert decision.reason_code == "approval-required"


@pytest.mark.parametrize("raw", [None, {"schema_version": 1}, {"schema_version": 9}])
def test_missing_or_invalid_policy_fails_closed(raw: dict | None) -> None:
    with pytest.raises(PolicyEvaluationError) as raised:
        evaluate(raw, context())
    assert raised.value.code == ErrorCode.POLICY_UNAVAILABLE


def test_expired_policy_fails_closed() -> None:
    raw = bundle()
    raw["valid_until"] = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(PolicyEvaluationError):
        evaluate(raw, context())
