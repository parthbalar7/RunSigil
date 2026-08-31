from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from runsigil_contracts import DecisionEffect, PolicyContext, PolicyDecision, canonical_digest
from runsigil_contracts.errors import ErrorCode, RunSigilError


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    action_type: str = Field(min_length=1, max_length=200)
    environments: list[str] = Field(min_length=1)
    risks: list[Literal["low", "medium", "high", "critical"]] = Field(min_length=1)
    maximum_amount_minor: int | None = Field(default=None, ge=0)
    effect: DecisionEffect
    reason: str = Field(min_length=1, max_length=500)


class PolicyBundleDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    enabled: bool = True
    valid_until: datetime | None = None
    default_effect: Literal[DecisionEffect.DENY] = DecisionEffect.DENY
    rules: list[PolicyRule]


class PolicyEvaluationError(RunSigilError):
    pass


def evaluate(raw_bundle: dict[str, Any] | None, context: PolicyContext) -> PolicyDecision:
    now = context.occurred_at.astimezone(UTC)
    if raw_bundle is None:
        raise PolicyEvaluationError(
            ErrorCode.POLICY_UNAVAILABLE,
            "No active policy bundle covers this action.",
            status_code=503,
        )
    try:
        bundle = PolicyBundleDocument.model_validate(raw_bundle)
    except ValidationError as exc:
        raise PolicyEvaluationError(
            ErrorCode.POLICY_UNAVAILABLE,
            "The active policy bundle is invalid.",
            status_code=503,
        ) from exc
    if not bundle.enabled:
        raise PolicyEvaluationError(
            ErrorCode.POLICY_UNAVAILABLE,
            "The active policy bundle is disabled.",
            status_code=503,
        )
    if bundle.valid_until is not None and bundle.valid_until.astimezone(UTC) <= now:
        raise PolicyEvaluationError(
            ErrorCode.POLICY_UNAVAILABLE,
            "The active policy bundle has expired.",
            status_code=503,
        )

    digest = canonical_digest(bundle)
    for rule in bundle.rules:
        if rule.action_type != context.action_type:
            continue
        if context.environment not in rule.environments or context.risk not in rule.risks:
            continue
        if (
            rule.maximum_amount_minor is not None
            and context.amount_minor > rule.maximum_amount_minor
        ):
            continue
        return PolicyDecision(
            effect=rule.effect,
            reason_code=rule.id,
            reason=rule.reason,
            policy_digest=digest,
            expires_at=now + timedelta(minutes=10),
        )

    return PolicyDecision(
        effect=DecisionEffect.DENY,
        reason_code="default_deny",
        reason="No policy rule authorizes this action.",
        policy_digest=digest,
        expires_at=now + timedelta(minutes=10),
    )
