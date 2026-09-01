from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from runsigil_contracts import WorkflowDefinition, WorkflowValidationResult


class WorkflowCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{0,99}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    definition: WorkflowDefinition


class WorkflowVersionCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: WorkflowDefinition


class WorkflowVersionSummary(BaseModel):
    id: UUID
    workflow_id: UUID
    version: int
    status: str
    definition: WorkflowDefinition
    definition_digest: str
    validation: WorkflowValidationResult
    created_by: UUID
    created_at: datetime


class WorkflowSummary(BaseModel):
    id: UUID
    project_id: UUID
    slug: str
    name: str
    description: str
    latest_version: WorkflowVersionSummary
    created_at: datetime
    updated_at: datetime


class WorkflowDeploymentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: UUID
    agent_id: UUID


class WorkflowDeploymentSummary(BaseModel):
    id: UUID
    workflow_version_id: UUID
    environment_id: UUID
    agent_id: UUID
    status: str
    deployed_by: UUID
    deployed_at: datetime


class WorkflowRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any]
    idempotency_key: str = Field(min_length=8, max_length=200)


class WorkflowSimulationProfileCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    tool_id: UUID
    name: str = Field(min_length=1, max_length=200)


class WorkflowSimulationProfileSummary(BaseModel):
    id: UUID
    project_id: UUID
    tool_id: UUID
    name: str
    provider: str
    contract_version: str
    status: str
    content_digest: str
    created_by: UUID
    created_at: datetime


class WorkflowForkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    simulation_profile_id: UUID | None = None


class WorkflowReplayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    simulation_profile_id: UUID | None = None


class WorkflowNodeAttemptSummary(BaseModel):
    id: UUID
    node_id: str
    node_type: str
    attempt: int
    status: str
    input_digest: str
    output_digest: str | None
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None


class RunCheckpointSummary(BaseModel):
    id: UUID
    sequence: int
    node_id: str
    state_digest: str
    active_nodes: list[str]
    completed_nodes: list[str]
    path: list[str]
    content_digest: str
    parent_checkpoint_id: UUID | None
    created_at: datetime


class WorkflowWaitSummary(BaseModel):
    id: UUID
    run_id: UUID
    workflow_execution_id: UUID
    node_id: str
    sequence: int
    wait_type: str
    status: str
    resolution: str | None
    content_digest: str
    state_digest: str
    request_metadata: dict[str, Any]
    event_key: str | None
    due_at: datetime | None
    expires_at: datetime
    response_digest: str | None
    resolved_by: UUID | None
    resolved_at: datetime | None
    created_at: datetime


class WorkflowWaitDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_digest: str = Field(min_length=71, max_length=71)
    decision: Literal["approved", "denied"]


class WorkflowWaitInformationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_digest: str = Field(min_length=71, max_length=71)
    information: dict[str, Any]


class WorkflowWaitEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_digest: str = Field(min_length=71, max_length=71)
    event_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    payload: dict[str, Any]


class WorkflowSubworkflowCallSummary(BaseModel):
    id: UUID
    parent_workflow_execution_id: UUID
    parent_run_id: UUID
    node_id: str
    sequence: int
    deployment_id: UUID
    child_workflow_execution_id: UUID
    child_run_id: UUID
    result_state_key: str
    status: str
    input_state_digest: str
    child_execution_content_digest: str
    result_state_digest: str | None
    content_digest: str
    expires_at: datetime
    resolved_at: datetime | None
    created_at: datetime


class WorkflowToolCallSummary(BaseModel):
    id: UUID
    workflow_execution_id: UUID
    parent_run_id: UUID
    child_run_id: UUID
    action_id: UUID
    intent_id: UUID
    tool_id: UUID
    node_id: str
    sequence: int
    result_state_key: str
    status: str
    arguments_digest: str
    tool_digest: str
    action_content_digest: str
    result_digest: str | None
    content_digest: str
    expires_at: datetime
    resolved_at: datetime | None
    created_at: datetime


class WorkflowToolSimulationCallSummary(BaseModel):
    id: UUID
    workflow_execution_id: UUID
    run_id: UUID
    simulation_profile_id: UUID
    tool_id: UUID
    node_id: str
    sequence: int
    result_state_key: str
    status: Literal["completed"]
    arguments_digest: str
    tool_digest: str
    profile_digest: str
    result_digest: str
    content_digest: str
    completed_at: datetime
    created_at: datetime


class ModelCallSummary(BaseModel):
    id: UUID
    workflow_execution_id: UUID
    run_id: UUID
    model_route_id: UUID
    delegation_id: UUID
    policy_decision_id: UUID
    node_id: str
    sequence: int
    result_state_key: str
    status: str
    request_digest: str
    route_digest: str
    content_digest: str
    output_digest: str | None
    provider_reference: str | None
    max_output_tokens: int
    input_tokens: int | None
    output_tokens: int | None
    cost_minor: int | None
    execute_attempts: int
    reconcile_attempts: int
    expires_at: datetime
    completed_at: datetime | None
    error_code: str | None
    created_at: datetime


class InternalModelAuthorizationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_digest: str
    claim_token: str
    mode: Literal["execute", "reconcile"] = "execute"


class InternalModelAuthorizationResponse(BaseModel):
    authorized: Literal[True] = True
    organization_id: UUID
    run_id: UUID
    workload_subject: str
    audience: str
    content_digest: str
    request_digest: str
    model_route_id: UUID
    provider: str
    model: str
    decision_id: UUID
    delegation_id: UUID
    budget_reservation_ids: list[UUID]


class WorkflowPolicyDecisionSummary(BaseModel):
    id: UUID
    node_id: str
    sequence: int
    evaluation: int
    policy_bundle_id: UUID
    effect: str
    reason_code: str
    input_digest: str
    policy_digest: str
    content_digest: str
    expires_at: datetime
    created_at: datetime


class WorkflowReplaySummary(BaseModel):
    id: UUID
    source_workflow_execution_id: UUID
    source_run_id: UUID
    source_checkpoint_id: UUID
    replay_workflow_execution_id: UUID
    replay_run_id: UUID
    status: str
    source_state_digest: str
    source_path_digest: str
    replay_state_digest: str | None
    replay_path_digest: str | None
    content_digest: str
    completed_at: datetime | None
    created_at: datetime


class WorkflowExecutionSummary(BaseModel):
    id: UUID
    workflow_version_id: UUID
    deployment_id: UUID
    execution_mode: Literal["live", "simulation"]
    simulation_profile_id: UUID | None
    status: str
    content_digest: str
    state_digest: str
    current_nodes: list[str]
    completed_nodes: list[str]
    path: list[str]
    step_count: int
    max_steps: int
    deadline_at: datetime
    forked_from_checkpoint_id: UUID | None
    error_code: str | None
    attempts: list[WorkflowNodeAttemptSummary]
    checkpoints: list[RunCheckpointSummary]
    waits: list[WorkflowWaitSummary]
    subworkflows: list[WorkflowSubworkflowCallSummary]
    tool_calls: list[WorkflowToolCallSummary]
    tool_simulations: list[WorkflowToolSimulationCallSummary]
    model_calls: list[ModelCallSummary]
    policy_decisions: list[WorkflowPolicyDecisionSummary]
    replay: WorkflowReplaySummary | None


class EvaluationScenarioMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    tags: list[str] = Field(default_factory=list, max_length=50)


NodeId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")]


class EvaluationScenarioAssertions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_policy_nodes: list[NodeId] = Field(default_factory=list, max_length=100)
    forbidden_nodes: list[NodeId] = Field(default_factory=list, max_length=100)
    maximum_steps: int | None = Field(default=None, ge=1, le=10_000)


class EvaluationScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    name: str = Field(min_length=1, max_length=200)
    input: dict[str, Any]
    expected_output: dict[str, Any]
    expected_path: list[str] = Field(default_factory=list, max_length=10_000)
    metadata: EvaluationScenarioMetadata = Field(default_factory=EvaluationScenarioMetadata)
    assertions: EvaluationScenarioAssertions = Field(default_factory=EvaluationScenarioAssertions)


class EvaluationDatasetCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{0,99}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    scenarios: list[EvaluationScenarioInput] = Field(min_length=1, max_length=1_000)


class EvaluationDatasetSummary(BaseModel):
    id: UUID
    project_id: UUID
    slug: str
    name: str
    description: str
    version_id: UUID
    version: int
    scenario_count: int
    content_digest: str
    created_at: datetime


class EvaluationCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: UUID
    dataset_version_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    minimum_score_milli: int = Field(default=1_000, ge=0, le=1_000)
    maximum_regression_milli: int = Field(default=0, ge=0, le=1_000)
    baseline_evaluation_id: UUID | None = None
    simulation_profile_id: UUID | None = None


ReasonCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")]


class EvaluationAnnotationCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=200)
    label: Literal["passed", "failed", "needs_review"]
    score_milli: int | None = Field(default=None, ge=0, le=1_000)
    reason_codes: list[ReasonCode] = Field(min_length=1, max_length=20)


class EvaluationAnnotationSummary(BaseModel):
    id: UUID
    evaluation_result_id: UUID
    evaluation_id: UUID
    scenario_id: UUID
    run_id: UUID
    reviewer_id: UUID
    label: str
    score_milli: int | None
    reason_codes: list[str]
    content_digest: str
    created_at: datetime


class EvaluationResultSummary(BaseModel):
    id: UUID
    scenario_id: UUID
    run_id: UUID
    status: str
    score_milli: int
    task_outcome: str
    trajectory_outcome: str
    deterministic_environment_outcome: str
    policy_outcome: str
    safety_outcome: str
    output_digest: str
    trajectory_digest: str
    graders: list[dict[str, Any]]
    annotations: list[EvaluationAnnotationSummary]


class EvaluationSummary(BaseModel):
    id: UUID
    workflow_version_id: UUID
    dataset_version_id: UUID
    deployment_id: UUID
    baseline_evaluation_id: UUID | None
    simulation_profile_id: UUID | None
    status: str
    minimum_score_milli: int
    maximum_regression_milli: int
    score_milli: int | None
    baseline_score_milli: int | None
    score_delta_milli: int | None
    regression_status: str | None
    release_gate_status: str
    content_digest: str
    completed_at: datetime | None
    results: list[EvaluationResultSummary]
