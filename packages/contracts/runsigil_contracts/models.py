from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DecisionEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_INFORMATION = "require_information"
    TRANSFORM = "transform"
    REDACT = "redact"
    RATE_LIMIT = "rate_limit"
    QUARANTINE = "quarantine"


class PolicyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(min_length=1, max_length=200)
    resource: str = Field(min_length=1, max_length=200)
    environment: str = Field(min_length=1, max_length=50)
    risk: Literal["low", "medium", "high", "critical"]
    data_classification: Literal["public", "internal", "confidential", "restricted"]
    actor_type: Literal["user", "service", "workload"]
    amount_minor: int = Field(default=0, ge=0)
    occurred_at: datetime


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effect: DecisionEffect
    reason_code: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    policy_digest: str
    expires_at: datetime


class ActionExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: UUID
    organization_id: UUID
    run_id: UUID
    content_digest: str
    idempotency_key: str
    claim_token: str
    arguments: dict[str, Any]


class ActionExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["committed", "failed", "ambiguous"]
    receipt_preview: dict[str, Any] = Field(default_factory=dict)
    provider_reference: str | None = None
    error_code: str | None = None
