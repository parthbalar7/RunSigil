from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GovernedActionArguments(BaseModel):
    """Content accepted by every governed-action ingress protocol."""

    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    environment_id: UUID
    agent_id: UUID
    recipient: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    amount_cents: int = Field(gt=0, le=100_000)
    description: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=200)
    simulate_outcome: Literal["committed", "ambiguous_after_commit", "failed"] = "committed"


class ContentBoundDecisionArguments(BaseModel):
    """Exact-content approval input shared by API, MCP, and A2A."""

    model_config = ConfigDict(extra="forbid")

    content_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    decision: Literal["approve", "deny"]
    reason: str = Field(min_length=2, max_length=500)
