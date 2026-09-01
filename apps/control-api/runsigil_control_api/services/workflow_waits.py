from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from runsigil_contracts import (
    WorkflowNode,
    WorkflowNodeType,
    canonical_digest,
    canonical_json_value,
)
from runsigil_contracts.crypto import decode_aes256_key, open_json, seal_json
from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import select
from sqlalchemy.orm import Session

from runsigil_control_api.models import OutboxEvent, Run, WorkflowExecution, WorkflowWait
from runsigil_control_api.services.governed_actions import _audit, _trace, database_now
from runsigil_control_api.settings import get_settings

if TYPE_CHECKING:
    from runsigil_control_api.auth import AuthContext
    from runsigil_control_api.services.workflows import WorkflowCryptoSettings


WAIT_NODE_TYPES = {
    WorkflowNodeType.TIMER,
    WorkflowNodeType.EVENT,
    WorkflowNodeType.APPROVAL,
    WorkflowNodeType.REQUEST_INFORMATION,
}


def _response_aad(wait: WorkflowWait) -> dict[str, str]:
    if wait.response_digest is None:
        raise ValueError("workflow wait response digest is unavailable")
    return {
        "organization_id": str(wait.organization_id),
        "workflow_wait_id": str(wait.id),
        "content_digest": wait.content_digest,
        "response_digest": wait.response_digest,
    }


def _response_digest(
    wait: WorkflowWait,
    *,
    resolution: str,
    payload_digest: str | None,
    resolved_by: UUID | None,
) -> str:
    return canonical_digest(
        {
            "workflow_wait_id": wait.id,
            "content_digest": wait.content_digest,
            "resolution": resolution,
            "payload_digest": payload_digest,
            "resolved_by": resolved_by,
        }
    )


def workflow_wait_summary(wait: WorkflowWait) -> dict[str, Any]:
    return {
        "id": wait.id,
        "run_id": wait.run_id,
        "workflow_execution_id": wait.workflow_execution_id,
        "node_id": wait.node_id,
        "sequence": wait.sequence,
        "wait_type": wait.wait_type,
        "status": wait.status,
        "resolution": wait.resolution,
        "content_digest": wait.content_digest,
        "state_digest": wait.state_digest,
        "request_metadata": wait.request_metadata_json,
        "event_key": wait.event_key,
        "due_at": wait.due_at,
        "expires_at": wait.expires_at,
        "response_digest": wait.response_digest,
        "resolved_by": wait.resolved_by,
        "resolved_at": wait.resolved_at,
        "created_at": wait.created_at,
    }


def create_workflow_wait(
    session: Session,
    *,
    execution: WorkflowExecution,
    run: Run,
    node: WorkflowNode,
    current_event: OutboxEvent,
    now: datetime,
) -> WorkflowWait:
    if node.type not in WAIT_NODE_TYPES:
        raise ValueError("node is not a durable wait")
    timeout_at = min(
        now + timedelta(seconds=node.timeout_seconds),
        execution.deadline_at,
    )
    due_at = (
        now + timedelta(seconds=int(node.config["delay_seconds"]))
        if node.type == WorkflowNodeType.TIMER
        else None
    )
    expires_at = due_at if due_at is not None else timeout_at
    wait_id = uuid4()
    request_metadata: dict[str, Any] = {"node_name": node.name}
    if node.type == WorkflowNodeType.APPROVAL:
        request_metadata.update(
            {"risk": node.config["risk"], "reason_code": node.config["reason_code"]}
        )
    elif node.type == WorkflowNodeType.REQUEST_INFORMATION:
        request_metadata.update(
            {
                "reason_code": node.config["reason_code"],
                "state_key": node.config["state_key"],
            }
        )
    elif node.type == WorkflowNodeType.EVENT:
        request_metadata.update(
            {"event_key": node.config["event_key"], "state_key": node.config["state_key"]}
        )
    else:
        request_metadata["delay_seconds"] = node.config["delay_seconds"]
    content_digest = canonical_digest(
        {
            "organization_id": execution.organization_id,
            "workflow_execution_id": execution.id,
            "run_id": execution.run_id,
            "node_id": node.id,
            "sequence": execution.step_count,
            "wait_type": node.type.value,
            "state_digest": execution.state_digest,
            "request_metadata": request_metadata,
            "due_at": due_at,
            "expires_at": expires_at,
        }
    )
    wait = WorkflowWait(
        id=wait_id,
        organization_id=execution.organization_id,
        workflow_execution_id=execution.id,
        run_id=execution.run_id,
        node_id=node.id,
        sequence=execution.step_count,
        wait_type=node.type.value,
        status="pending",
        resolution=None,
        content_digest=content_digest,
        state_digest=execution.state_digest,
        request_metadata_json=canonical_json_value(request_metadata),
        event_key=(str(node.config["event_key"]) if node.type == WorkflowNodeType.EVENT else None),
        due_at=due_at,
        expires_at=expires_at,
        response_digest=None,
        encrypted_response=None,
        resolved_by=None,
        resolved_at=None,
    )
    session.add(wait)
    execution.status = "waiting"
    execution.version += 1
    execution.claim_token_hash = None
    execution.lease_expires_at = None
    run.status = "waiting_for_approval" if node.type == WorkflowNodeType.APPROVAL else "waiting"
    run.active_node = node.id
    current_event.processed_at = now
    session.add(
        OutboxEvent(
            id=uuid4(),
            organization_id=execution.organization_id,
            topic="workflow.ready",
            aggregate_type="workflow_execution",
            aggregate_id=execution.id,
            deduplication_key=f"workflow.wait:{wait.id}:wake",
            payload_json={
                "workflow_execution_id": str(execution.id),
                "workflow_wait_id": str(wait.id),
                "wait_sequence": wait.sequence,
                "content_digest": wait.content_digest,
            },
            available_at=expires_at,
            attempts=0,
        )
    )
    _trace(
        session,
        organization_id=execution.organization_id,
        run_id=execution.run_id,
        node_id=node.id,
        event_type="workflow.wait_created",
        status="waiting",
        attributes={
            "workflow_wait_id": str(wait.id),
            "wait_type": wait.wait_type,
            "content_digest": wait.content_digest,
            "expires_at": wait.expires_at.isoformat(),
            "raw_content_captured": False,
        },
    )
    _audit(
        session,
        organization_id=execution.organization_id,
        actor_id=run.actor_id,
        event_type="workflow.wait_created",
        subject_type="workflow_wait",
        subject_id=wait.id,
        content_digest=wait.content_digest,
        metadata={
            "run_id": str(wait.run_id),
            "node_id": wait.node_id,
            "wait_type": wait.wait_type,
            "raw_content_captured": False,
        },
    )
    return wait


def resolve_timer_wait(wait: WorkflowWait, *, now: datetime) -> None:
    wait.status = "resolved"
    wait.resolution = "elapsed"
    wait.response_digest = _response_digest(
        wait,
        resolution="elapsed",
        payload_digest=None,
        resolved_by=None,
    )
    wait.resolved_at = now


def verify_wait_resolution(wait: WorkflowWait) -> None:
    if wait.resolution is None or wait.response_digest is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The workflow wait resolution is incomplete.",
            status_code=409,
        )
    if wait.wait_type in {
        WorkflowNodeType.EVENT.value,
        WorkflowNodeType.REQUEST_INFORMATION.value,
    }:
        return
    expected = _response_digest(
        wait,
        resolution=wait.resolution,
        payload_digest=None,
        resolved_by=wait.resolved_by,
    )
    if not hmac.compare_digest(expected, wait.response_digest):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The workflow wait resolution digest does not match.",
            status_code=409,
        )


def decrypt_wait_response(
    wait: WorkflowWait,
    settings: WorkflowCryptoSettings | None = None,
) -> dict[str, Any]:
    if wait.encrypted_response is None or wait.response_digest is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The workflow wait response is unavailable.",
            status_code=409,
        )
    resolved = settings or get_settings()
    payload = open_json(
        wait.encrypted_response,
        key=decode_aes256_key(resolved.action_encryption_key_b64),
        associated_data=_response_aad(wait),
    )
    if not isinstance(payload, dict):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The workflow wait response is invalid.",
            status_code=409,
        )
    expected = _response_digest(
        wait,
        resolution=str(wait.resolution),
        payload_digest=canonical_digest(payload),
        resolved_by=wait.resolved_by,
    )
    if not hmac.compare_digest(expected, wait.response_digest):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The workflow wait response digest does not match.",
            status_code=409,
        )
    return payload


def resolve_workflow_wait(
    session: Session,
    *,
    context: AuthContext,
    wait_id: UUID,
    expected_type: str,
    submitted_content_digest: str,
    resolution: str,
    payload: dict[str, Any] | None = None,
    event_key: str | None = None,
) -> WorkflowWait:
    timeout_event = session.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.deduplication_key == f"workflow.wait:{wait_id}:wake")
        .with_for_update()
    )
    wait = session.scalar(select(WorkflowWait).where(WorkflowWait.id == wait_id).with_for_update())
    if wait is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Workflow wait not found.", status_code=404)
    if wait.wait_type != expected_type:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The workflow wait does not accept this response type.",
            status_code=409,
        )
    if wait.status != "pending":
        raise RunSigilError(
            ErrorCode.APPROVAL_REPLAYED,
            "The workflow wait has already been resolved and cannot be replayed.",
            status_code=409,
        )
    now = database_now(session)
    if wait.expires_at <= now:
        raise RunSigilError(
            ErrorCode.APPROVAL_EXPIRED,
            "The workflow wait has expired.",
            status_code=409,
        )
    if not hmac.compare_digest(submitted_content_digest, wait.content_digest):
        raise RunSigilError(
            ErrorCode.APPROVAL_DIGEST_MISMATCH,
            "The workflow wait content digest does not match.",
            status_code=409,
        )
    if expected_type == WorkflowNodeType.EVENT.value and (
        event_key is None or not hmac.compare_digest(event_key, wait.event_key or "")
    ):
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "The workflow event key does not match the pending wait.",
            status_code=409,
        )
    execution = session.get(WorkflowExecution, wait.workflow_execution_id)
    run = session.get(Run, wait.run_id)
    if (
        execution is None
        or run is None
        or execution.status != "waiting"
        or wait.node_id not in execution.current_nodes_json
        or wait.sequence != execution.step_count
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The workflow execution is no longer waiting at this node.",
            status_code=409,
        )
    payload_digest = canonical_digest(payload) if payload is not None else None
    response_digest = _response_digest(
        wait,
        resolution=resolution,
        payload_digest=payload_digest,
        resolved_by=context.actor_id,
    )
    wait.status = "resolved"
    wait.resolution = resolution
    wait.response_digest = response_digest
    wait.resolved_by = context.actor_id
    wait.resolved_at = now
    if payload is not None:
        wait.encrypted_response = seal_json(
            payload,
            key=decode_aes256_key(get_settings().action_encryption_key_b64),
            associated_data=_response_aad(wait),
        )
    if timeout_event is not None and timeout_event.processed_at is None:
        timeout_event.processed_at = now
    session.add(
        OutboxEvent(
            id=uuid4(),
            organization_id=wait.organization_id,
            topic="workflow.ready",
            aggregate_type="workflow_execution",
            aggregate_id=execution.id,
            deduplication_key=f"workflow.wait:{wait.id}:resume",
            payload_json={
                "workflow_execution_id": str(execution.id),
                "workflow_wait_id": str(wait.id),
                "wait_sequence": wait.sequence,
                "content_digest": wait.content_digest,
            },
            available_at=now,
            attempts=0,
        )
    )
    _trace(
        session,
        organization_id=wait.organization_id,
        run_id=wait.run_id,
        node_id=wait.node_id,
        event_type="workflow.wait_resolved",
        status="completed",
        attributes={
            "workflow_wait_id": str(wait.id),
            "wait_type": wait.wait_type,
            "resolution": resolution,
            "response_digest": response_digest,
            "raw_content_captured": False,
        },
    )
    _audit(
        session,
        organization_id=wait.organization_id,
        actor_id=context.actor_id,
        event_type="workflow.wait_resolved",
        subject_type="workflow_wait",
        subject_id=wait.id,
        content_digest=wait.content_digest,
        metadata={
            "resolution": resolution,
            "response_digest": response_digest,
            "raw_content_captured": False,
        },
    )
    return wait
