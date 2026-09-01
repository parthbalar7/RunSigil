from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError
from runsigil_contracts import WorkflowNode, canonical_digest, canonical_json_value
from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import select
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Action,
    ApprovalRequest,
    EvidenceBundle,
    Intent,
    OutboxEvent,
    Run,
    Tool,
    WorkflowExecution,
    WorkflowToolCall,
)
from runsigil_control_api.schemas import GovernedActionInput
from runsigil_control_api.services.budgets import release_action_reservations
from runsigil_control_api.services.governed_actions import (
    TOOL_NAME,
    _audit,
    _trace,
    create_governed_action,
)


class WorkflowToolSettings(Protocol):
    action_encryption_key_b64: str
    approval_ttl_seconds: int


@dataclass(frozen=True)
class _ToolActorContext:
    organization_id: UUID
    actor_id: UUID
    actor_type: str


TERMINAL_TOOL_CALL_STATUSES = frozenset({"completed", "failed", "cancelled", "timed_out"})


def tool_document(tool: Tool) -> dict[str, Any]:
    return {
        "id": tool.id,
        "name": tool.name,
        "effect_class": tool.effect_class,
        "connector": tool.connector,
        "risk": tool.risk,
        "input_schema": canonical_json_value(tool.input_schema_json),
    }


def workflow_tool_call_summary(row: WorkflowToolCall) -> dict[str, Any]:
    return {
        "id": row.id,
        "workflow_execution_id": row.workflow_execution_id,
        "parent_run_id": row.parent_run_id,
        "child_run_id": row.child_run_id,
        "action_id": row.action_id,
        "intent_id": row.intent_id,
        "tool_id": row.tool_id,
        "node_id": row.node_id,
        "sequence": row.sequence,
        "result_state_key": row.result_state_key,
        "status": row.status,
        "arguments_digest": row.arguments_digest,
        "tool_digest": row.tool_digest,
        "action_content_digest": row.action_content_digest,
        "result_digest": row.result_digest,
        "content_digest": row.content_digest,
        "expires_at": row.expires_at,
        "resolved_at": row.resolved_at,
        "created_at": row.created_at,
    }


def safe_tool_result(action: Action) -> dict[str, Any]:
    return {
        "action_id": str(action.id),
        "run_id": str(action.run_id),
        "outcome": action.state,
        "provider_reference": action.provider_reference,
        "receipt_preview": canonical_json_value(action.receipt_preview_json or {}),
        "content_digest": action.content_digest,
    }


def create_workflow_tool_call(
    session: Session,
    *,
    execution: WorkflowExecution,
    parent_run: Run,
    node: WorkflowNode,
    state: dict[str, Any],
    current_event: OutboxEvent,
    now: datetime,
    settings: WorkflowToolSettings,
) -> WorkflowToolCall:
    tool_id = UUID(str(node.config["tool_id"]))
    tool = session.get(Tool, tool_id)
    if (
        tool is None
        or tool.name != TOOL_NAME
        or tool.effect_class != "transactional"
        or tool.connector != "runsigil-demo-provider-v1"
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The referenced tool is unavailable or is not supported by this executor.",
            status_code=409,
        )
    arguments_key = str(node.config["arguments_state_key"])
    raw_arguments = state.get(arguments_key)
    if not isinstance(raw_arguments, dict):
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "The tool arguments state key must contain an object.",
            status_code=422,
        )
    idempotency_key = f"workflow-tool:{execution.id}:{node.id}:{execution.step_count}"
    try:
        request = GovernedActionInput.model_validate(
            {
                **raw_arguments,
                "project_id": parent_run.project_id,
                "environment_id": parent_run.environment_id,
                "agent_id": parent_run.agent_id,
                "idempotency_key": idempotency_key,
            }
        )
    except ValidationError as exc:
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "The encrypted workflow state does not satisfy the tool input contract.",
            status_code=422,
            details={"issues": exc.errors(include_input=False, include_url=False)},
        ) from exc
    child_run = create_governed_action(
        session,
        context=_ToolActorContext(
            organization_id=execution.organization_id,
            actor_id=parent_run.actor_id,
            actor_type=parent_run.actor_type,
        ),
        request=request,
        settings=settings,
    )
    action = session.scalar(select(Action).where(Action.run_id == child_run.id))
    if action is None:
        raise RuntimeError("governed workflow tool action was not persisted")
    intent = session.get(Intent, action.intent_id)
    if intent is None:
        raise RuntimeError("governed workflow tool intent was not persisted")
    expires_at = min(
        now + timedelta(seconds=node.timeout_seconds),
        execution.deadline_at,
    )
    tool_digest = canonical_digest(tool_document(tool))
    content_digest = canonical_digest(
        {
            "organization_id": execution.organization_id,
            "workflow_execution_id": execution.id,
            "parent_run_id": execution.run_id,
            "child_run_id": child_run.id,
            "action_id": action.id,
            "intent_id": intent.id,
            "tool_id": tool.id,
            "tool_digest": tool_digest,
            "node_id": node.id,
            "sequence": execution.step_count,
            "result_state_key": node.config["result_state_key"],
            "arguments_digest": intent.arguments_digest,
            "action_content_digest": action.content_digest,
            "expires_at": expires_at,
        }
    )
    call = WorkflowToolCall(
        id=uuid4(),
        organization_id=execution.organization_id,
        workflow_execution_id=execution.id,
        parent_run_id=execution.run_id,
        child_run_id=child_run.id,
        action_id=action.id,
        intent_id=intent.id,
        tool_id=tool.id,
        node_id=node.id,
        sequence=execution.step_count,
        result_state_key=str(node.config["result_state_key"]),
        status="pending_approval" if action.state == "proposed" else "queued",
        arguments_digest=intent.arguments_digest,
        tool_digest=tool_digest,
        action_content_digest=action.content_digest,
        result_digest=None,
        content_digest=content_digest,
        expires_at=expires_at,
        resolved_at=None,
    )
    session.add(call)
    session.flush()
    session.add(
        OutboxEvent(
            id=uuid4(),
            organization_id=execution.organization_id,
            topic="workflow.ready",
            aggregate_type="workflow_execution",
            aggregate_id=execution.id,
            deduplication_key=f"workflow.tool:{call.id}:timeout",
            payload_json={
                "workflow_execution_id": str(execution.id),
                "workflow_tool_call_id": str(call.id),
                "content_digest": call.content_digest,
                "reason": "timeout",
            },
            available_at=expires_at,
            attempts=0,
        )
    )
    execution.status = "waiting"
    execution.claim_token_hash = None
    execution.lease_expires_at = None
    parent_run.status = "waiting"
    parent_run.active_node = node.id
    current_event.processed_at = now
    _trace(
        session,
        organization_id=execution.organization_id,
        run_id=execution.run_id,
        node_id=node.id,
        event_type="workflow.tool_requested",
        status=call.status,
        attributes={
            "workflow_tool_call_id": str(call.id),
            "child_run_id": str(child_run.id),
            "action_id": str(action.id),
            "tool_id": str(tool.id),
            "tool_digest": tool_digest,
            "arguments_digest": intent.arguments_digest,
            "action_content_digest": action.content_digest,
            "raw_content_captured": False,
        },
    )
    _audit(
        session,
        organization_id=execution.organization_id,
        actor_id=parent_run.actor_id,
        event_type="workflow.tool_requested",
        subject_type="workflow_tool_call",
        subject_id=call.id,
        content_digest=call.content_digest,
        metadata={
            "workflow_execution_id": str(execution.id),
            "parent_run_id": str(execution.run_id),
            "child_run_id": str(child_run.id),
            "action_id": str(action.id),
            "status": call.status,
            "raw_content_captured": False,
        },
    )
    return call


def lock_tool_timeout_event(
    session: Session,
    call_id: UUID,
) -> OutboxEvent | None:
    return session.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.deduplication_key == f"workflow.tool:{call_id}:timeout")
        .with_for_update()
    )


def tool_call_id_for_action(session: Session, action_id: UUID) -> UUID | None:
    return session.scalar(
        select(WorkflowToolCall.id).where(WorkflowToolCall.action_id == action_id)
    )


def settle_workflow_tool_call(
    session: Session,
    *,
    call: WorkflowToolCall,
    action: Action,
    now: datetime,
) -> None:
    if call.status in TERMINAL_TOOL_CALL_STATUSES:
        return
    if action.state == "committed":
        result = safe_tool_result(action)
        call.status = "completed"
        call.result_digest = canonical_digest(result)
        call.resolved_at = now
    elif action.state == "failed":
        call.status = "failed"
        call.resolved_at = now
    elif action.state == "dead_lettered":
        call.status = "dead_lettered"
        return
    elif action.state == "reconciliation_required":
        call.status = "reconciliation_required"
        return
    else:
        return
    session.add(
        OutboxEvent(
            id=uuid4(),
            organization_id=call.organization_id,
            topic="workflow.ready",
            aggregate_type="workflow_execution",
            aggregate_id=call.workflow_execution_id,
            deduplication_key=f"workflow.tool:{call.id}:resume:{call.status}",
            payload_json={
                "workflow_execution_id": str(call.workflow_execution_id),
                "workflow_tool_call_id": str(call.id),
                "content_digest": call.content_digest,
                "reason": "settled",
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
        event_type="workflow.tool_settled",
        status=call.status,
        attributes={
            "workflow_tool_call_id": str(call.id),
            "child_run_id": str(call.child_run_id),
            "action_id": str(action.id),
            "result_digest": call.result_digest,
            "raw_content_captured": False,
        },
    )
    parent_run = session.get(Run, call.parent_run_id)
    if parent_run is not None:
        _audit(
            session,
            organization_id=call.organization_id,
            actor_id=parent_run.actor_id,
            event_type="workflow.tool_settled",
            subject_type="workflow_tool_call",
            subject_id=call.id,
            content_digest=call.content_digest,
            metadata={
                "workflow_execution_id": str(call.workflow_execution_id),
                "child_run_id": str(call.child_run_id),
                "action_id": str(action.id),
                "status": call.status,
                "result_digest": call.result_digest,
                "raw_content_captured": False,
            },
        )


def reject_workflow_tool_call(
    session: Session,
    *,
    call: WorkflowToolCall,
    now: datetime,
    error_code: str,
) -> None:
    if call.status in TERMINAL_TOOL_CALL_STATUSES:
        return
    timeout_event = lock_tool_timeout_event(session, call.id)
    if timeout_event is not None and timeout_event.processed_at is None:
        timeout_event.processed_at = now
    call.status = "failed"
    call.resolved_at = now
    session.add(
        OutboxEvent(
            id=uuid4(),
            organization_id=call.organization_id,
            topic="workflow.ready",
            aggregate_type="workflow_execution",
            aggregate_id=call.workflow_execution_id,
            deduplication_key=f"workflow.tool:{call.id}:resume:failed",
            payload_json={
                "workflow_execution_id": str(call.workflow_execution_id),
                "workflow_tool_call_id": str(call.id),
                "content_digest": call.content_digest,
                "reason": error_code,
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
        event_type="workflow.tool_rejected",
        status="failed",
        attributes={
            "workflow_tool_call_id": str(call.id),
            "child_run_id": str(call.child_run_id),
            "action_id": str(call.action_id),
            "error_code": error_code,
            "raw_content_captured": False,
        },
    )


def expire_workflow_tool_call(
    session: Session,
    *,
    call_id: UUID,
    now: datetime,
) -> WorkflowToolCall:
    snapshot = session.get(WorkflowToolCall, call_id)
    if snapshot is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The workflow tool call disappeared before timeout settlement.",
            status_code=409,
        )
    action_ready = session.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.aggregate_id == snapshot.action_id,
            OutboxEvent.topic == "action.ready",
        )
        .order_by(OutboxEvent.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    call = session.scalar(
        select(WorkflowToolCall).where(WorkflowToolCall.id == call_id).with_for_update()
    )
    if call is None:
        raise RuntimeError("workflow tool call disappeared while locking timeout")
    if call.status in TERMINAL_TOOL_CALL_STATUSES:
        return call
    action_snapshot = session.get(Action, call.action_id)
    approval = (
        session.scalar(
            select(ApprovalRequest)
            .where(ApprovalRequest.id == action_snapshot.approval_request_id)
            .with_for_update()
        )
        if action_snapshot is not None
        and action_snapshot.state == "proposed"
        and action_snapshot.approval_request_id is not None
        else None
    )
    action = session.scalar(select(Action).where(Action.id == call.action_id).with_for_update())
    child_run = session.get(Run, call.child_run_id)
    if action is None or child_run is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The governed tool action lineage is incomplete.",
            status_code=409,
        )
    if action.state in {"executing", "reconciling", "reconciliation_required"}:
        call.status = (
            "reconciliation_required" if action.state == "reconciliation_required" else action.state
        )
        return call
    if action.state in {"committed", "failed", "dead_lettered"}:
        settle_workflow_tool_call(session, call=call, action=action, now=now)
        return call
    safely_cancelable = (
        action.state == "proposed" and approval is not None and approval.status == "pending"
    ) or (
        action.state == "approved"
        and action_ready is not None
        and action_ready.dispatched_at is None
    )
    if not safely_cancelable:
        call.status = "reconciliation_required"
        return call
    if approval is not None:
        approval.status = "expired"
        approval.decided_at = now
        approval.decision_reason = "workflow tool node timeout"
    if action_ready is not None:
        action_ready.dispatched_at = action_ready.dispatched_at or now
        action_ready.processed_at = now
        action_ready.attempts += 1
    action.state = "rejected"
    action.error_code = "workflow_tool_timed_out_before_effect"
    action.version += 1
    child_run.status = "cancelled"
    child_run.error_code = action.error_code
    child_run.active_node = None
    child_run.completed_at = now
    release_action_reservations(
        session,
        organization_id=call.organization_id,
        action_id=action.id,
        now=now,
    )
    call.status = "timed_out"
    call.resolved_at = now
    _trace(
        session,
        organization_id=call.organization_id,
        run_id=call.parent_run_id,
        node_id=call.node_id,
        event_type="workflow.tool_timed_out",
        status="timed_out",
        attributes={
            "workflow_tool_call_id": str(call.id),
            "child_run_id": str(call.child_run_id),
            "action_id": str(call.action_id),
            "side_effect_started": False,
            "raw_content_captured": False,
        },
    )
    return call


def cancel_pending_workflow_tool_call(
    session: Session,
    *,
    call: WorkflowToolCall,
    actor_id: UUID,
    now: datetime,
) -> None:
    action = session.scalar(select(Action).where(Action.id == call.action_id).with_for_update())
    approval = (
        session.scalar(
            select(ApprovalRequest)
            .where(ApprovalRequest.id == action.approval_request_id)
            .with_for_update()
        )
        if action is not None and action.approval_request_id is not None
        else None
    )
    child_run = session.get(Run, call.child_run_id)
    if (
        call.status != "pending_approval"
        or action is None
        or action.state != "proposed"
        or approval is None
        or approval.status != "pending"
        or child_run is None
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The workflow tool call is no longer at a cancelable pre-effect boundary.",
            status_code=409,
        )
    approval.status = "denied"
    approval.decided_at = now
    approval.decided_by = actor_id
    approval.decision_reason = "parent workflow cancelled"
    action.state = "rejected"
    action.version += 1
    child_run.status = "cancelled"
    child_run.error_code = "parent_workflow_cancelled"
    child_run.active_node = None
    child_run.completed_at = now
    release_action_reservations(
        session,
        organization_id=call.organization_id,
        action_id=action.id,
        now=now,
    )
    call.status = "cancelled"
    call.resolved_at = now


def terminal_tool_call_evidence_digest(
    session: Session,
    call: WorkflowToolCall,
) -> str | None:
    evidence = session.scalar(
        select(EvidenceBundle).where(EvidenceBundle.run_id == call.child_run_id)
    )
    return evidence.content_digest if evidence is not None else None
