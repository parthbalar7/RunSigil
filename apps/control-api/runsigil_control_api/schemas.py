from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from runsigil_contracts import ContentBoundDecisionArguments, GovernedActionArguments

from runsigil_control_api.workflow_schemas import WorkflowExecutionSummary


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ContextResponse(BaseModel):
    organization: dict[str, Any]
    projects: list[dict[str, Any]]
    environments: list[dict[str, Any]]
    systems: list[dict[str, Any]]
    agents: list[dict[str, Any]]


class GovernedActionInput(GovernedActionArguments):
    pass


class ApprovalDecisionInput(ContentBoundDecisionArguments):
    pass


class ApprovalSummary(BaseModel):
    id: UUID
    run_id: UUID
    status: str
    risk: str
    reason: str
    content_digest: str
    request_preview: dict[str, Any]
    expires_at: datetime


class ActionSummary(BaseModel):
    id: UUID
    tool_name: str
    state: str
    content_digest: str
    request_preview: dict[str, Any]
    receipt_preview: dict[str, Any] | None
    execute_attempts: int
    reconcile_attempts: int
    reconcile_cycle_attempts: int
    error_code: str | None


class TraceEventSummary(BaseModel):
    id: UUID
    node_id: str
    span_id: str
    trace_id: str
    event_type: str
    status: str
    sequence: int
    attributes: dict[str, Any]
    created_at: datetime


class RunDetail(BaseModel):
    id: UUID
    status: str
    project_id: UUID
    environment_id: UUID
    agent_id: UUID
    active_node: str | None
    input_digest: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    run_kind: str
    action: ActionSummary | None
    approval: ApprovalSummary | None
    workflow: WorkflowExecutionSummary | None
    trace_events: list[TraceEventSummary]
    evidence_status: str


class RunListPage(BaseModel):
    items: list[RunDetail]
    next_cursor: str | None
    page_size: int
    total: int


class CursorPage(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None


class DeadLetterSummary(BaseModel):
    id: UUID
    action_id: UUID
    run_id: UUID
    source: str
    reason_code: str
    status: str
    attempt_count: int
    redrive_count: int
    max_redrives: int
    version: int
    content_digest: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class DeadLetterPage(BaseModel):
    items: list[DeadLetterSummary]
    next_cursor: str | None


class DeadLetterRedriveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=500)


class InternalAuthorizationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_digest: str
    claim_token: str
    mode: Literal["execute", "reconcile"] = "execute"


class InternalAuthorizationResponse(BaseModel):
    authorized: Literal[True] = True
    organization_id: UUID
    run_id: UUID
    workload_subject: str
    audience: str
    content_digest: str
    arguments_digest: str
    decision_id: UUID
    approval_id: UUID | None
    budget_reservation_id: UUID
    budget_reservation_ids: list[UUID]
