from __future__ import annotations

import hmac
from uuid import UUID

from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import select
from sqlalchemy.orm import Session

from runsigil_control_api.auth import AuthContext
from runsigil_control_api.models import Action, DeadLetter, Run, WorkflowToolCall
from runsigil_control_api.services.governed_actions import _audit, _trace, database_now


def redrive_dead_letter(
    session: Session,
    *,
    context: AuthContext,
    dead_letter_id: UUID,
    expected_version: int,
    reason: str,
) -> DeadLetter:
    dead_letter_candidate = session.scalar(
        select(DeadLetter).where(DeadLetter.id == dead_letter_id)
    )
    if dead_letter_candidate is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Dead letter not found.", status_code=404)

    tool_call_id = session.scalar(
        select(WorkflowToolCall.id).where(
            WorkflowToolCall.action_id == dead_letter_candidate.action_id
        )
    )
    tool_call: WorkflowToolCall | None = None
    if tool_call_id is not None:
        from runsigil_control_api.services.workflow_tools import lock_tool_timeout_event

        lock_tool_timeout_event(session, tool_call_id)
        tool_call = session.scalar(
            select(WorkflowToolCall).where(WorkflowToolCall.id == tool_call_id).with_for_update()
        )
    action = session.scalar(
        select(Action).where(Action.id == dead_letter_candidate.action_id).with_for_update()
    )
    run = session.scalar(
        select(Run).where(Run.id == dead_letter_candidate.run_id).with_for_update()
    )
    dead_letter = session.scalar(
        select(DeadLetter).where(DeadLetter.id == dead_letter_id).with_for_update()
    )
    if dead_letter is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Dead letter not found.", status_code=404)
    if dead_letter.status != "open":
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "Only an open dead letter can be redriven.",
            status_code=409,
        )
    if dead_letter.version != expected_version:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The dead letter version is stale.",
            status_code=409,
            details={"current_version": dead_letter.version},
        )
    if dead_letter.redrive_count >= dead_letter.max_redrives:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The bounded dead-letter redrive limit is exhausted.",
            status_code=409,
        )
    if (
        action is None
        or run is None
        or action.state != "dead_lettered"
        or run.status != "dead_lettered"
        or action.lease_expires_at is not None
        or not hmac.compare_digest(action.content_digest, dead_letter.content_digest)
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "Dead-letter lineage is incomplete, stale, or currently claimed.",
            status_code=409,
        )

    now = database_now(session)
    dead_letter.status = "redriven"
    dead_letter.redrive_count += 1
    dead_letter.version += 1
    dead_letter.resolved_at = None
    action.state = "reconciliation_required"
    action.reconcile_cycle_attempts = 0
    action.next_reconcile_at = now
    action.error_code = "dlq_redrive_reconciliation_pending"
    action.version += 1
    run.status = "reconciliation_required"
    run.active_node = "action-reconciliation"
    run.error_code = action.error_code
    if tool_call is not None:
        if tool_call.status != "dead_lettered":
            raise RunSigilError(
                ErrorCode.INVALID_TRANSITION,
                "Workflow tool-call dead-letter lineage is stale.",
                status_code=409,
            )
        tool_call.status = "reconciliation_required"

    _trace(
        session,
        organization_id=context.organization_id,
        run_id=run.id,
        node_id="action-reconciliation",
        event_type="dead_letter.redriven",
        status="queued",
        attributes={
            "dead_letter_id": str(dead_letter.id),
            "redrive_count": dead_letter.redrive_count,
            "max_redrives": dead_letter.max_redrives,
            "reconcile_only": True,
        },
    )
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="dead_letter.redriven",
        subject_type="dead_letter",
        subject_id=dead_letter.id,
        content_digest=dead_letter.content_digest,
        metadata={
            "run_id": str(run.id),
            "action_id": str(action.id),
            "redrive_count": dead_letter.redrive_count,
            "max_redrives": dead_letter.max_redrives,
            "reason": reason,
            "reconcile_only": True,
        },
    )
    return dead_letter


def dead_letter_summary(row: DeadLetter) -> dict[str, object]:
    return {
        "id": row.id,
        "action_id": row.action_id,
        "run_id": row.run_id,
        "source": row.source,
        "reason_code": row.reason_code,
        "status": row.status,
        "attempt_count": row.attempt_count,
        "redrive_count": row.redrive_count,
        "max_redrives": row.max_redrives,
        "version": row.version,
        "content_digest": row.content_digest,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "resolved_at": row.resolved_at,
    }
