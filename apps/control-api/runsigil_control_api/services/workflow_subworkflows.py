from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from runsigil_contracts import WorkflowNode, canonical_digest
from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import select
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    OutboxEvent,
    Run,
    Workflow,
    WorkflowDeployment,
    WorkflowExecution,
    WorkflowSubworkflowCall,
    WorkflowVersion,
)
from runsigil_control_api.services.governed_actions import _audit, _trace
from runsigil_control_api.services.workflows import _create_execution_records

if TYPE_CHECKING:
    from runsigil_control_api.services.workflows import WorkflowCryptoSettings


@dataclass
class _ChildActorContext:
    organization_id: UUID
    actor_id: UUID
    actor_type: str


def subworkflow_call_summary(row: WorkflowSubworkflowCall) -> dict[str, Any]:
    return {
        "id": row.id,
        "parent_workflow_execution_id": row.parent_workflow_execution_id,
        "parent_run_id": row.parent_run_id,
        "node_id": row.node_id,
        "sequence": row.sequence,
        "deployment_id": row.deployment_id,
        "child_workflow_execution_id": row.child_workflow_execution_id,
        "child_run_id": row.child_run_id,
        "result_state_key": row.result_state_key,
        "status": row.status,
        "input_state_digest": row.input_state_digest,
        "child_execution_content_digest": row.child_execution_content_digest,
        "result_state_digest": row.result_state_digest,
        "content_digest": row.content_digest,
        "expires_at": row.expires_at,
        "resolved_at": row.resolved_at,
        "created_at": row.created_at,
    }


def create_subworkflow_call(
    session: Session,
    *,
    parent_execution: WorkflowExecution,
    parent_run: Run,
    node: WorkflowNode,
    state: dict[str, Any],
    current_event: OutboxEvent,
    now: datetime,
    settings: WorkflowCryptoSettings,
) -> WorkflowSubworkflowCall:
    deployment_id = UUID(str(node.config["deployment_id"]))
    child_deployment = session.get(WorkflowDeployment, deployment_id)
    parent_deployment = session.get(WorkflowDeployment, parent_execution.deployment_id)
    child_version = (
        session.get(WorkflowVersion, child_deployment.workflow_version_id)
        if child_deployment is not None
        else None
    )
    child_workflow = (
        session.get(Workflow, child_version.workflow_id) if child_version is not None else None
    )
    parent_version = session.get(WorkflowVersion, parent_execution.workflow_version_id)
    parent_workflow = (
        session.get(Workflow, parent_version.workflow_id) if parent_version is not None else None
    )
    if (
        child_deployment is None
        or child_deployment.status not in {"active", "superseded"}
        or parent_deployment is None
        or child_version is None
        or child_workflow is None
        or parent_workflow is None
        or child_workflow.project_id != parent_workflow.project_id
        or child_deployment.environment_id != parent_deployment.environment_id
        or child_deployment.agent_id != parent_deployment.agent_id
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The referenced subworkflow deployment is unavailable or outside the parent scope.",
            status_code=409,
        )
    child_run = _create_execution_records(
        session,
        context=_ChildActorContext(
            organization_id=parent_execution.organization_id,
            actor_id=parent_run.actor_id,
            actor_type=parent_run.actor_type,
        ),
        deployment=child_deployment,
        state=dict(state),
        idempotency_key=(
            f"subworkflow:{parent_execution.id}:{node.id}:{parent_execution.step_count}"
        ),
        execution_purpose="subworkflow",
        settings=settings,
    )
    child_execution = session.scalar(
        select(WorkflowExecution).where(WorkflowExecution.run_id == child_run.id)
    )
    if child_execution is None:
        raise RuntimeError("subworkflow child execution was not persisted")
    expires_at = min(
        now + timedelta(seconds=node.timeout_seconds),
        parent_execution.deadline_at,
    )
    content_digest = canonical_digest(
        {
            "organization_id": parent_execution.organization_id,
            "parent_workflow_execution_id": parent_execution.id,
            "parent_run_id": parent_execution.run_id,
            "node_id": node.id,
            "sequence": parent_execution.step_count,
            "deployment_id": child_deployment.id,
            "child_workflow_version_id": child_version.id,
            "child_definition_digest": child_version.definition_digest,
            "child_workflow_execution_id": child_execution.id,
            "child_run_id": child_run.id,
            "child_execution_content_digest": child_execution.content_digest,
            "input_state_digest": parent_execution.state_digest,
            "result_state_key": node.config["result_state_key"],
            "expires_at": expires_at,
        }
    )
    call = WorkflowSubworkflowCall(
        id=uuid4(),
        organization_id=parent_execution.organization_id,
        parent_workflow_execution_id=parent_execution.id,
        parent_run_id=parent_execution.run_id,
        node_id=node.id,
        sequence=parent_execution.step_count,
        deployment_id=child_deployment.id,
        child_workflow_execution_id=child_execution.id,
        child_run_id=child_run.id,
        result_state_key=str(node.config["result_state_key"]),
        status="pending",
        input_state_digest=parent_execution.state_digest,
        child_execution_content_digest=child_execution.content_digest,
        result_state_digest=None,
        content_digest=content_digest,
        expires_at=expires_at,
        resolved_at=None,
    )
    session.add(call)
    parent_execution.status = "waiting"
    parent_execution.version += 1
    parent_execution.claim_token_hash = None
    parent_execution.lease_expires_at = None
    parent_run.status = "waiting"
    parent_run.active_node = node.id
    current_event.processed_at = now
    session.add(
        OutboxEvent(
            id=uuid4(),
            organization_id=parent_execution.organization_id,
            topic="workflow.ready",
            aggregate_type="workflow_execution",
            aggregate_id=parent_execution.id,
            deduplication_key=f"subworkflow.call:{call.id}:timeout",
            payload_json={
                "workflow_execution_id": str(parent_execution.id),
                "workflow_subworkflow_call_id": str(call.id),
                "content_digest": call.content_digest,
            },
            available_at=expires_at,
            attempts=0,
        )
    )
    _trace(
        session,
        organization_id=call.organization_id,
        run_id=call.parent_run_id,
        node_id=call.node_id,
        event_type="workflow.subworkflow_started",
        status="waiting",
        attributes={
            "subworkflow_call_id": str(call.id),
            "child_run_id": str(call.child_run_id),
            "child_workflow_execution_id": str(call.child_workflow_execution_id),
            "content_digest": call.content_digest,
            "raw_content_captured": False,
        },
    )
    _audit(
        session,
        organization_id=call.organization_id,
        actor_id=parent_run.actor_id,
        event_type="workflow.subworkflow_started",
        subject_type="workflow_subworkflow_call",
        subject_id=call.id,
        content_digest=call.content_digest,
        metadata={
            "parent_run_id": str(call.parent_run_id),
            "child_run_id": str(call.child_run_id),
            "node_id": call.node_id,
            "raw_content_captured": False,
        },
    )
    return call


def settle_parent_subworkflow_call(
    session: Session,
    *,
    child_execution: WorkflowExecution,
    child_run: Run,
    now: datetime,
) -> WorkflowSubworkflowCall | None:
    call_id = session.scalar(
        select(WorkflowSubworkflowCall.id).where(
            WorkflowSubworkflowCall.child_workflow_execution_id == child_execution.id
        )
    )
    if call_id is None:
        return None
    timeout_event = session.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.deduplication_key == f"subworkflow.call:{call_id}:timeout")
        .with_for_update()
    )
    call = session.scalar(
        select(WorkflowSubworkflowCall)
        .where(WorkflowSubworkflowCall.id == call_id)
        .with_for_update()
    )
    if call is None or call.status != "pending":
        return call
    if timeout_event is not None and timeout_event.processed_at is None:
        timeout_event.processed_at = now
    if child_execution.status == "completed":
        call.status = "completed"
        call.result_state_digest = child_execution.state_digest
    elif child_execution.status == "cancelled":
        call.status = "cancelled"
        call.result_state_digest = None
    else:
        call.status = "failed"
        call.result_state_digest = None
    call.resolved_at = now
    session.add(
        OutboxEvent(
            id=uuid4(),
            organization_id=call.organization_id,
            topic="workflow.ready",
            aggregate_type="workflow_execution",
            aggregate_id=call.parent_workflow_execution_id,
            deduplication_key=f"subworkflow.call:{call.id}:resume",
            payload_json={
                "workflow_execution_id": str(call.parent_workflow_execution_id),
                "workflow_subworkflow_call_id": str(call.id),
                "content_digest": call.content_digest,
            },
            available_at=now,
            attempts=0,
        )
    )
    _trace(
        session,
        organization_id=call.organization_id,
        run_id=call.parent_run_id,
        node_id=call.node_id,
        event_type="workflow.subworkflow_settled",
        status=call.status,
        attributes={
            "subworkflow_call_id": str(call.id),
            "child_run_id": str(child_run.id),
            "status": call.status,
            "result_state_digest": call.result_state_digest,
            "raw_content_captured": False,
        },
    )
    return call
