from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from runsigil_control_api.models.base import Base, IdMixin, TenantMixin, TimestampMixin


class Workflow(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "project_id", "slug"),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
        ),
    )

    project_id: Mapped[UUID] = mapped_column()
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(500))


class WorkflowVersion(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "workflow_id", "version"),
        ForeignKeyConstraint(
            ["organization_id", "workflow_id"],
            ["workflows.organization_id", "workflows.id"],
            ondelete="RESTRICT",
        ),
    )

    workflow_id: Mapped[UUID] = mapped_column()
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    definition_digest: Mapped[str] = mapped_column(String(71))
    validation_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_by: Mapped[UUID] = mapped_column()


class WorkflowDeployment(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_deployments"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "workflow_version_id", "environment_id", "agent_id"),
        ForeignKeyConstraint(
            ["organization_id", "workflow_version_id"],
            ["workflow_versions.organization_id", "workflow_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "environment_id"],
            ["environments.organization_id", "environments.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "agent_id"],
            ["agents.organization_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )

    workflow_version_id: Mapped[UUID] = mapped_column()
    environment_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(30))
    deployed_by: Mapped[UUID] = mapped_column()
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkflowSimulationProfile(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_simulation_profiles"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "project_id", "name"),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "tool_id"],
            ["tools.organization_id", "tools.id"],
            ondelete="RESTRICT",
        ),
    )

    project_id: Mapped[UUID] = mapped_column()
    tool_id: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(200))
    contract_version: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30))
    content_digest: Mapped[str] = mapped_column(String(71))
    created_by: Mapped[UUID] = mapped_column()


class WorkflowExecution(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_executions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "run_id"),
        UniqueConstraint(
            "organization_id", "id", "run_id", name="uq_workflow_execution_identity_run"
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            "evaluation_id",
            "evaluation_scenario_id",
            "run_id",
            name="uq_workflow_execution_eval_lineage",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_version_id"],
            ["workflow_versions.organization_id", "workflow_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "deployment_id"],
            ["workflow_deployments.organization_id", "workflow_deployments.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evaluation_id"],
            ["evaluations.organization_id", "evaluations.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evaluation_scenario_id"],
            ["evaluation_scenarios.organization_id", "evaluation_scenarios.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "forked_from_checkpoint_id"],
            ["run_checkpoints.organization_id", "run_checkpoints.id"],
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_workflow_executions_organization_id_run_checkpoints",
        ),
        ForeignKeyConstraint(
            ["organization_id", "simulation_profile_id"],
            ["workflow_simulation_profiles.organization_id", "workflow_simulation_profiles.id"],
            ondelete="RESTRICT",
            name="fk_workflow_execution_simulation_profile",
        ),
    )

    run_id: Mapped[UUID] = mapped_column()
    workflow_version_id: Mapped[UUID] = mapped_column()
    deployment_id: Mapped[UUID] = mapped_column()
    evaluation_id: Mapped[UUID | None] = mapped_column()
    evaluation_scenario_id: Mapped[UUID | None] = mapped_column()
    forked_from_checkpoint_id: Mapped[UUID | None] = mapped_column()
    execution_mode: Mapped[str] = mapped_column(String(30), default="live", server_default="live")
    simulation_profile_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_digest: Mapped[str] = mapped_column(String(71))
    encrypted_state: Mapped[str] = mapped_column(Text)
    state_digest: Mapped[str] = mapped_column(String(71))
    current_nodes_json: Mapped[list[str]] = mapped_column(JSON)
    completed_nodes_json: Mapped[list[str]] = mapped_column(JSON)
    path_json: Mapped[list[str]] = mapped_column(JSON)
    loop_counts_json: Mapped[dict[str, int]] = mapped_column(JSON)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    max_steps: Mapped[int] = mapped_column(Integer)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    worker_name: Mapped[str | None] = mapped_column(String(200))
    claim_token_hash: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))


class WorkflowWait(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_waits"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "workflow_execution_id", "node_id", "sequence"),
        ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_wait_execution_run",
        ),
    )

    workflow_execution_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    node_id: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)
    wait_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30))
    resolution: Mapped[str | None] = mapped_column(String(30))
    content_digest: Mapped[str] = mapped_column(String(71))
    state_digest: Mapped[str] = mapped_column(String(71))
    request_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    event_key: Mapped[str | None] = mapped_column(String(100))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_digest: Mapped[str | None] = mapped_column(String(71))
    encrypted_response: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[UUID | None] = mapped_column()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowSubworkflowCall(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_subworkflow_calls"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint(
            "organization_id",
            "parent_workflow_execution_id",
            "node_id",
            "sequence",
        ),
        ForeignKeyConstraint(
            ["organization_id", "parent_workflow_execution_id", "parent_run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_subworkflow_call_parent_execution_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "child_workflow_execution_id", "child_run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_subworkflow_call_child_execution_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "deployment_id"],
            ["workflow_deployments.organization_id", "workflow_deployments.id"],
            ondelete="RESTRICT",
        ),
    )

    parent_workflow_execution_id: Mapped[UUID] = mapped_column()
    parent_run_id: Mapped[UUID] = mapped_column()
    node_id: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)
    deployment_id: Mapped[UUID] = mapped_column()
    child_workflow_execution_id: Mapped[UUID] = mapped_column()
    child_run_id: Mapped[UUID] = mapped_column()
    result_state_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    input_state_digest: Mapped[str] = mapped_column(String(71))
    child_execution_content_digest: Mapped[str] = mapped_column(String(71))
    result_state_digest: Mapped[str | None] = mapped_column(String(71))
    content_digest: Mapped[str] = mapped_column(String(71))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowToolCall(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_tool_calls"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint(
            "organization_id",
            "workflow_execution_id",
            "node_id",
            "sequence",
        ),
        UniqueConstraint("organization_id", "child_run_id"),
        UniqueConstraint("organization_id", "action_id"),
        ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "parent_run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_tool_call_parent_execution_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "child_run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_call_child_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "action_id", "child_run_id"],
            ["actions.organization_id", "actions.id", "actions.run_id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_call_action_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "intent_id", "child_run_id"],
            ["intents.organization_id", "intents.id", "intents.run_id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_call_intent_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "tool_id"],
            ["tools.organization_id", "tools.id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_call_tool",
        ),
    )

    workflow_execution_id: Mapped[UUID] = mapped_column()
    parent_run_id: Mapped[UUID] = mapped_column()
    child_run_id: Mapped[UUID] = mapped_column()
    action_id: Mapped[UUID] = mapped_column()
    intent_id: Mapped[UUID] = mapped_column()
    tool_id: Mapped[UUID] = mapped_column()
    node_id: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)
    result_state_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40))
    arguments_digest: Mapped[str] = mapped_column(String(71))
    tool_digest: Mapped[str] = mapped_column(String(71))
    action_content_digest: Mapped[str] = mapped_column(String(71))
    result_digest: Mapped[str | None] = mapped_column(String(71))
    content_digest: Mapped[str] = mapped_column(String(71))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowToolSimulationCall(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_tool_simulation_calls"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint(
            "organization_id",
            "workflow_execution_id",
            "node_id",
            "sequence",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_tool_simulation_execution_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "simulation_profile_id"],
            ["workflow_simulation_profiles.organization_id", "workflow_simulation_profiles.id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_simulation_profile",
        ),
        ForeignKeyConstraint(
            ["organization_id", "tool_id"],
            ["tools.organization_id", "tools.id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_simulation_tool",
        ),
    )

    workflow_execution_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    simulation_profile_id: Mapped[UUID] = mapped_column()
    tool_id: Mapped[UUID] = mapped_column()
    node_id: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)
    result_state_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    arguments_digest: Mapped[str] = mapped_column(String(71))
    tool_digest: Mapped[str] = mapped_column(String(71))
    profile_digest: Mapped[str] = mapped_column(String(71))
    result_digest: Mapped[str] = mapped_column(String(71))
    content_digest: Mapped[str] = mapped_column(String(71))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ModelCall(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "model_calls"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint(
            "organization_id",
            "workflow_execution_id",
            "node_id",
            "sequence",
        ),
        UniqueConstraint("organization_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_model_call_execution_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "model_route_id"],
            ["model_routes.organization_id", "model_routes.id"],
            ondelete="RESTRICT",
            name="fk_model_call_route",
        ),
        ForeignKeyConstraint(
            ["organization_id", "delegation_id"],
            ["delegations.organization_id", "delegations.id"],
            ondelete="RESTRICT",
            name="fk_model_call_delegation",
        ),
        ForeignKeyConstraint(
            ["organization_id", "policy_decision_id"],
            ["workflow_policy_decisions.organization_id", "workflow_policy_decisions.id"],
            ondelete="RESTRICT",
            name="fk_model_call_policy_decision",
        ),
    )

    workflow_execution_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    model_route_id: Mapped[UUID] = mapped_column()
    delegation_id: Mapped[UUID] = mapped_column()
    policy_decision_id: Mapped[UUID] = mapped_column()
    node_id: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)
    input_state_key: Mapped[str] = mapped_column(String(100))
    result_state_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40))
    request_digest: Mapped[str] = mapped_column(String(71))
    route_digest: Mapped[str] = mapped_column(String(71))
    content_digest: Mapped[str] = mapped_column(String(71))
    encrypted_request: Mapped[str] = mapped_column(Text)
    output_digest: Mapped[str | None] = mapped_column(String(71))
    encrypted_output: Mapped[str | None] = mapped_column(Text)
    provider_reference: Mapped[str | None] = mapped_column(String(300))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    worker_name: Mapped[str | None] = mapped_column(String(200))
    claim_token_hash: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execute_attempts: Mapped[int] = mapped_column(Integer, default=0)
    reconcile_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))


class ModelCallBudgetReservation(Base, TenantMixin, TimestampMixin):
    __tablename__ = "model_call_budget_reservations"
    __table_args__ = (
        UniqueConstraint("organization_id", "model_call_id", "budget_reservation_id"),
        ForeignKeyConstraint(
            ["organization_id", "model_call_id"],
            ["model_calls.organization_id", "model_calls.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "budget_reservation_id"],
            ["budget_reservations.organization_id", "budget_reservations.id"],
            ondelete="RESTRICT",
        ),
    )

    model_call_id: Mapped[UUID] = mapped_column(primary_key=True)
    budget_reservation_id: Mapped[UUID] = mapped_column(primary_key=True)


class WorkflowNodeAttempt(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_node_attempts"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "workflow_execution_id", "node_id", "attempt"),
        ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_node_attempt_execution_run",
        ),
    )

    workflow_execution_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    node_id: Mapped[str] = mapped_column(String(100))
    node_type: Mapped[str] = mapped_column(String(40))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    input_digest: Mapped[str] = mapped_column(String(71))
    output_digest: Mapped[str | None] = mapped_column(String(71))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))


class RunCheckpoint(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "run_checkpoints"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint(
            "organization_id",
            "id",
            "workflow_execution_id",
            "run_id",
            name="uq_run_checkpoint_replay_lineage",
        ),
        UniqueConstraint("organization_id", "run_id", "sequence"),
        ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_run_checkpoint_execution_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "parent_checkpoint_id"],
            ["run_checkpoints.organization_id", "run_checkpoints.id"],
            ondelete="RESTRICT",
        ),
    )

    workflow_execution_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    sequence: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String(100))
    encrypted_state: Mapped[str] = mapped_column(Text)
    state_digest: Mapped[str] = mapped_column(String(71))
    active_nodes_json: Mapped[list[str]] = mapped_column(JSON)
    completed_nodes_json: Mapped[list[str]] = mapped_column(JSON)
    path_json: Mapped[list[str]] = mapped_column(JSON)
    loop_counts_json: Mapped[dict[str, int]] = mapped_column(JSON)
    content_digest: Mapped[str] = mapped_column(String(71))
    parent_checkpoint_id: Mapped[UUID | None] = mapped_column()


class EvaluationDataset(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "project_id", "slug"),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
        ),
    )

    project_id: Mapped[UUID] = mapped_column()
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(500))


class EvaluationDatasetVersion(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "evaluation_dataset_versions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "dataset_id", "version"),
        ForeignKeyConstraint(
            ["organization_id", "dataset_id"],
            ["evaluation_datasets.organization_id", "evaluation_datasets.id"],
            ondelete="RESTRICT",
        ),
    )

    dataset_id: Mapped[UUID] = mapped_column()
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    content_digest: Mapped[str] = mapped_column(String(71))
    scenario_count: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[UUID] = mapped_column()


class EvaluationScenario(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "evaluation_scenarios"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "dataset_version_id", "scenario_key"),
        ForeignKeyConstraint(
            ["organization_id", "dataset_version_id"],
            ["evaluation_dataset_versions.organization_id", "evaluation_dataset_versions.id"],
            ondelete="RESTRICT",
        ),
    )

    dataset_version_id: Mapped[UUID] = mapped_column()
    scenario_key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    input_digest: Mapped[str] = mapped_column(String(71))
    expected_output_digest: Mapped[str] = mapped_column(String(71))
    expected_path_json: Mapped[list[str]] = mapped_column(JSON)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_digest: Mapped[str] = mapped_column(String(71))


class Evaluation(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["organization_id", "workflow_version_id"],
            ["workflow_versions.organization_id", "workflow_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "dataset_version_id"],
            ["evaluation_dataset_versions.organization_id", "evaluation_dataset_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "deployment_id"],
            ["workflow_deployments.organization_id", "workflow_deployments.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "baseline_evaluation_id"],
            ["evaluations.organization_id", "evaluations.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "simulation_profile_id"],
            ["workflow_simulation_profiles.organization_id", "workflow_simulation_profiles.id"],
            ondelete="RESTRICT",
            name="fk_evaluation_simulation_profile",
        ),
    )

    workflow_version_id: Mapped[UUID] = mapped_column()
    dataset_version_id: Mapped[UUID] = mapped_column()
    deployment_id: Mapped[UUID] = mapped_column()
    baseline_evaluation_id: Mapped[UUID | None] = mapped_column()
    simulation_profile_id: Mapped[UUID | None] = mapped_column()
    actor_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    minimum_score_milli: Mapped[int] = mapped_column(Integer)
    maximum_regression_milli: Mapped[int] = mapped_column(Integer)
    score_milli: Mapped[int | None] = mapped_column(Integer)
    baseline_score_milli: Mapped[int | None] = mapped_column(Integer)
    score_delta_milli: Mapped[int | None] = mapped_column(Integer)
    regression_status: Mapped[str | None] = mapped_column(String(30))
    release_gate_status: Mapped[str] = mapped_column(String(30))
    content_digest: Mapped[str] = mapped_column(String(71))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationResult(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "evaluation_id", "scenario_id"),
        UniqueConstraint(
            "organization_id",
            "id",
            "evaluation_id",
            "scenario_id",
            "run_id",
            name="uq_evaluation_result_annotation_lineage",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evaluation_id"],
            ["evaluations.organization_id", "evaluations.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "scenario_id"],
            ["evaluation_scenarios.organization_id", "evaluation_scenarios.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "organization_id",
                "workflow_execution_id",
                "evaluation_id",
                "scenario_id",
                "run_id",
            ],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.evaluation_id",
                "workflow_executions.evaluation_scenario_id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_eval_result_execution_lineage",
        ),
    )

    evaluation_id: Mapped[UUID] = mapped_column()
    scenario_id: Mapped[UUID] = mapped_column()
    workflow_execution_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(30))
    score_milli: Mapped[int] = mapped_column(Integer)
    task_outcome: Mapped[str] = mapped_column(String(30))
    trajectory_outcome: Mapped[str] = mapped_column(String(30))
    deterministic_environment_outcome: Mapped[str] = mapped_column(String(30))
    policy_outcome: Mapped[str] = mapped_column(String(30))
    safety_outcome: Mapped[str] = mapped_column(String(30))
    output_digest: Mapped[str] = mapped_column(String(71))
    trajectory_digest: Mapped[str] = mapped_column(String(71))
    graders_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)


class WorkflowPolicyDecision(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_policy_decisions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint(
            "organization_id",
            "workflow_execution_id",
            "node_id",
            "sequence",
            "evaluation",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_policy_decision_execution_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "policy_bundle_id"],
            ["policy_bundles.organization_id", "policy_bundles.id"],
            ondelete="RESTRICT",
        ),
    )

    workflow_execution_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    node_id: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)
    evaluation: Mapped[int] = mapped_column(Integer)
    policy_bundle_id: Mapped[UUID] = mapped_column()
    effect: Mapped[str] = mapped_column(String(40))
    reason_code: Mapped[str] = mapped_column(String(100))
    input_digest: Mapped[str] = mapped_column(String(71))
    policy_digest: Mapped[str] = mapped_column(String(71))
    content_digest: Mapped[str] = mapped_column(String(71))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkflowReplay(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workflow_replays"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "replay_run_id"),
        ForeignKeyConstraint(
            ["organization_id", "source_workflow_execution_id", "source_run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_replay_source_execution_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "replay_workflow_execution_id", "replay_run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_replay_execution_run",
        ),
        ForeignKeyConstraint(
            [
                "organization_id",
                "source_checkpoint_id",
                "source_workflow_execution_id",
                "source_run_id",
            ],
            [
                "run_checkpoints.organization_id",
                "run_checkpoints.id",
                "run_checkpoints.workflow_execution_id",
                "run_checkpoints.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_replay_source_checkpoint",
        ),
    )

    source_workflow_execution_id: Mapped[UUID] = mapped_column()
    source_run_id: Mapped[UUID] = mapped_column()
    source_checkpoint_id: Mapped[UUID] = mapped_column()
    replay_workflow_execution_id: Mapped[UUID] = mapped_column()
    replay_run_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(30))
    source_state_digest: Mapped[str] = mapped_column(String(71))
    source_path_digest: Mapped[str] = mapped_column(String(71))
    replay_state_digest: Mapped[str | None] = mapped_column(String(71))
    replay_path_digest: Mapped[str | None] = mapped_column(String(71))
    content_digest: Mapped[str] = mapped_column(String(71))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationAnnotation(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "evaluation_annotations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "idempotency_key"),
        ForeignKeyConstraint(
            [
                "organization_id",
                "evaluation_result_id",
                "evaluation_id",
                "scenario_id",
                "run_id",
            ],
            [
                "evaluation_results.organization_id",
                "evaluation_results.id",
                "evaluation_results.evaluation_id",
                "evaluation_results.scenario_id",
                "evaluation_results.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_evaluation_annotation_result_lineage",
        ),
    )

    evaluation_result_id: Mapped[UUID] = mapped_column()
    evaluation_id: Mapped[UUID] = mapped_column()
    scenario_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    reviewer_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(30))
    score_milli: Mapped[int | None] = mapped_column(Integer)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON)
    content_digest: Mapped[str] = mapped_column(String(71))
