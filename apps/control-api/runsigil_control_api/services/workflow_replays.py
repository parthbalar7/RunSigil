from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from runsigil_contracts import canonical_digest
from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import select
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Run,
    RunCheckpoint,
    WorkflowDeployment,
    WorkflowExecution,
    WorkflowReplay,
)
from runsigil_control_api.services.governed_actions import _audit, _trace
from runsigil_control_api.services.workflow_simulation import require_simulation_profile
from runsigil_control_api.services.workflows import (
    WorkflowActorContext,
    _create_execution_records,
    decrypt_checkpoint_state,
)
from runsigil_control_api.workflow_schemas import WorkflowReplayInput


def workflow_replay_summary(row: WorkflowReplay) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_workflow_execution_id": row.source_workflow_execution_id,
        "source_run_id": row.source_run_id,
        "source_checkpoint_id": row.source_checkpoint_id,
        "replay_workflow_execution_id": row.replay_workflow_execution_id,
        "replay_run_id": row.replay_run_id,
        "status": row.status,
        "source_state_digest": row.source_state_digest,
        "source_path_digest": row.source_path_digest,
        "replay_state_digest": row.replay_state_digest,
        "replay_path_digest": row.replay_path_digest,
        "content_digest": row.content_digest,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
    }


def create_workflow_replay(
    session: Session,
    *,
    context: WorkflowActorContext,
    source_run_id: UUID,
    request: WorkflowReplayInput,
) -> Run:
    source = session.scalar(
        select(WorkflowExecution).where(WorkflowExecution.run_id == source_run_id)
    )
    if source is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Workflow run not found.", status_code=404)
    if source.status != "completed":
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "Only a completed workflow execution can be replayed.",
            status_code=409,
        )
    checkpoint = session.scalar(
        select(RunCheckpoint).where(
            RunCheckpoint.id == request.checkpoint_id,
            RunCheckpoint.workflow_execution_id == source.id,
            RunCheckpoint.run_id == source.run_id,
        )
    )
    if checkpoint is None:
        raise RunSigilError(
            ErrorCode.NOT_FOUND,
            "Workflow checkpoint not found for the source run.",
            status_code=404,
        )
    if not checkpoint.active_nodes_json:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "A terminal checkpoint has no remaining execution to replay.",
            status_code=409,
        )
    deployment = session.get(WorkflowDeployment, source.deployment_id)
    if deployment is None or deployment.status not in {"active", "superseded"}:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The source deployment is unavailable for replay.",
            status_code=409,
        )
    profile = require_simulation_profile(
        session,
        deployment=deployment,
        profile_id=request.simulation_profile_id,
    )
    state = decrypt_checkpoint_state(checkpoint)
    replay_run = _create_execution_records(
        session,
        context=context,
        deployment=deployment,
        state=state,
        idempotency_key=request.idempotency_key,
        forked_from_checkpoint_id=checkpoint.id,
        initial_nodes=checkpoint.active_nodes_json,
        completed_nodes=checkpoint.completed_nodes_json,
        path=checkpoint.path_json,
        loop_counts=checkpoint.loop_counts_json,
        execution_purpose="replay",
        execution_mode="simulation" if profile is not None else "live",
        simulation_profile_id=profile.id if profile is not None else None,
    )
    replay_execution = session.scalar(
        select(WorkflowExecution).where(WorkflowExecution.run_id == replay_run.id)
    )
    if replay_execution is None:
        raise RuntimeError("replay workflow execution was not persisted")
    existing = session.scalar(
        select(WorkflowReplay).where(WorkflowReplay.replay_run_id == replay_run.id)
    )
    expected_content_digest = canonical_digest(
        {
            "organization_id": source.organization_id,
            "source_workflow_execution_id": source.id,
            "source_run_id": source.run_id,
            "source_checkpoint_id": checkpoint.id,
            "replay_workflow_execution_id": replay_execution.id,
            "replay_run_id": replay_run.id,
            "source_state_digest": source.state_digest,
            "source_path_digest": canonical_digest(source.path_json),
            "simulation_profile_id": profile.id if profile is not None else None,
        }
    )
    if existing is not None:
        if not hmac.compare_digest(existing.content_digest, expected_content_digest):
            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "The idempotency key belongs to different replay content.",
                status_code=409,
            )
        return replay_run
    replay = WorkflowReplay(
        id=uuid4(),
        organization_id=source.organization_id,
        source_workflow_execution_id=source.id,
        source_run_id=source.run_id,
        source_checkpoint_id=checkpoint.id,
        replay_workflow_execution_id=replay_execution.id,
        replay_run_id=replay_run.id,
        status="running",
        source_state_digest=source.state_digest,
        source_path_digest=canonical_digest(source.path_json),
        replay_state_digest=None,
        replay_path_digest=None,
        content_digest=expected_content_digest,
        completed_at=None,
    )
    session.add(replay)
    _trace(
        session,
        organization_id=source.organization_id,
        run_id=replay_run.id,
        node_id="replay",
        event_type="workflow.replay_started",
        status="running",
        attributes={
            "workflow_replay_id": str(replay.id),
            "source_run_id": str(source.run_id),
            "source_checkpoint_id": str(checkpoint.id),
            "content_digest": replay.content_digest,
            "raw_content_captured": False,
        },
    )
    _audit(
        session,
        organization_id=source.organization_id,
        actor_id=context.actor_id,
        event_type="workflow.replay_started",
        subject_type="workflow_replay",
        subject_id=replay.id,
        content_digest=replay.content_digest,
        metadata={
            "source_run_id": str(source.run_id),
            "replay_run_id": str(replay_run.id),
            "source_checkpoint_id": str(checkpoint.id),
            "raw_content_captured": False,
        },
    )
    return replay_run


def settle_workflow_replay(
    session: Session,
    *,
    execution: WorkflowExecution,
    now: datetime,
) -> WorkflowReplay | None:
    replay = session.scalar(
        select(WorkflowReplay)
        .where(WorkflowReplay.replay_workflow_execution_id == execution.id)
        .with_for_update()
    )
    if replay is None or replay.status != "running":
        return replay
    if execution.status == "completed":
        replay.replay_state_digest = execution.state_digest
        replay.replay_path_digest = canonical_digest(execution.path_json)
        replay.status = (
            "matched"
            if hmac.compare_digest(replay.source_state_digest, replay.replay_state_digest)
            and hmac.compare_digest(replay.source_path_digest, replay.replay_path_digest)
            else "diverged"
        )
    elif execution.status == "cancelled":
        replay.status = "cancelled"
    else:
        replay.status = "failed"
    replay.completed_at = now
    _trace(
        session,
        organization_id=execution.organization_id,
        run_id=execution.run_id,
        node_id="replay",
        event_type="workflow.replay_settled",
        status=replay.status,
        attributes={
            "workflow_replay_id": str(replay.id),
            "source_run_id": str(replay.source_run_id),
            "status": replay.status,
            "replay_state_digest": replay.replay_state_digest,
            "replay_path_digest": replay.replay_path_digest,
            "raw_content_captured": False,
        },
    )
    return replay
