from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from runsigil_contracts import DecisionEffect, WorkflowNode, canonical_bytes, canonical_digest
from runsigil_contracts.crypto import decode_aes256_key, open_json, seal_json
from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import select
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Agent,
    Delegation,
    ModelCall,
    ModelRoute,
    OutboxEvent,
    Run,
    WorkflowExecution,
    WorkflowPolicyDecision,
    WorkloadIdentity,
)
from runsigil_control_api.services.budgets import (
    BudgetContext,
    link_model_call_reservations,
    model_call_reservations,
    reserve_budgets,
    settle_model_call_reservations,
)
from runsigil_control_api.services.governed_actions import _audit, _trace, database_now
from runsigil_control_api.workflow_schemas import InternalModelAuthorizationResponse

MODEL_ACTION_TYPE = "model.generate"
MODEL_PROVIDER = "demo"
MODEL_NAME = "demo-governed-model"
MODEL_AUDIENCE = "runsigil-demo-provider"


class ModelCryptoSettings(Protocol):
    action_encryption_key_b64: str


def model_route_document(route: ModelRoute) -> dict[str, Any]:
    return {
        "id": route.id,
        "project_id": route.project_id,
        "name": route.name,
        "provider": route.provider,
        "model": route.model,
        "status": route.status,
    }


def _model_call_aad(row: ModelCall, content_kind: str) -> dict[str, str]:
    return {
        "organization_id": str(row.organization_id),
        "model_call_id": str(row.id),
        "content_digest": row.content_digest,
        "content_kind": content_kind,
    }


def decrypt_model_request(
    row: ModelCall,
    settings: ModelCryptoSettings,
) -> dict[str, Any]:
    value = open_json(
        row.encrypted_request,
        key=decode_aes256_key(settings.action_encryption_key_b64),
        associated_data=_model_call_aad(row, "request"),
    )
    if not isinstance(value, dict) or not hmac.compare_digest(
        canonical_digest(value), row.request_digest
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The encrypted model request is invalid or has been modified.",
            status_code=409,
        )
    return value


def decrypt_model_output(
    row: ModelCall,
    settings: ModelCryptoSettings,
) -> dict[str, Any]:
    if row.encrypted_output is None or row.output_digest is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The model output is incomplete.",
            status_code=409,
        )
    value = open_json(
        row.encrypted_output,
        key=decode_aes256_key(settings.action_encryption_key_b64),
        associated_data=_model_call_aad(row, "output"),
    )
    if not isinstance(value, dict) or not hmac.compare_digest(
        canonical_digest(value), row.output_digest
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The encrypted model output is invalid or has been modified.",
            status_code=409,
        )
    return value


def encrypt_model_output(
    row: ModelCall,
    output: dict[str, Any],
    settings: ModelCryptoSettings,
) -> str:
    return seal_json(
        output,
        key=decode_aes256_key(settings.action_encryption_key_b64),
        associated_data=_model_call_aad(row, "output"),
    )


def model_call_summary(row: ModelCall) -> dict[str, Any]:
    return {
        "id": row.id,
        "workflow_execution_id": row.workflow_execution_id,
        "run_id": row.run_id,
        "model_route_id": row.model_route_id,
        "delegation_id": row.delegation_id,
        "policy_decision_id": row.policy_decision_id,
        "node_id": row.node_id,
        "sequence": row.sequence,
        "result_state_key": row.result_state_key,
        "status": row.status,
        "request_digest": row.request_digest,
        "route_digest": row.route_digest,
        "content_digest": row.content_digest,
        "output_digest": row.output_digest,
        "provider_reference": row.provider_reference,
        "max_output_tokens": row.max_output_tokens,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cost_minor": row.cost_minor,
        "execute_attempts": row.execute_attempts,
        "reconcile_attempts": row.reconcile_attempts,
        "expires_at": row.expires_at,
        "completed_at": row.completed_at,
        "error_code": row.error_code,
        "created_at": row.created_at,
    }


def create_workflow_model_call(
    session: Session,
    *,
    execution: WorkflowExecution,
    run: Run,
    node: WorkflowNode,
    policy_decision: WorkflowPolicyDecision | None,
    state: dict[str, Any],
    current_event: OutboxEvent,
    now: datetime,
    settings: ModelCryptoSettings,
) -> ModelCall:
    if execution.execution_mode != "live":
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "Agent model calls are not executed inside tool-simulation runs.",
            status_code=409,
        )
    if policy_decision is None or policy_decision.effect != DecisionEffect.ALLOW.value:
        raise RunSigilError(
            ErrorCode.POLICY_DENIED,
            "An agent node requires an exact allow policy decision.",
            status_code=403,
        )
    if node.model_route_id is None:
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "The agent node model route is missing.",
            status_code=422,
        )
    route = session.get(ModelRoute, node.model_route_id)
    if (
        route is None
        or route.status != "active"
        or route.project_id != run.project_id
        or route.provider != MODEL_PROVIDER
        or route.model != MODEL_NAME
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The referenced model route is unavailable or unsupported.",
            status_code=409,
        )
    raw_request = state.get(str(node.config["input_state_key"]))
    if not isinstance(raw_request, dict):
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "The agent input state key must contain an object.",
            status_code=422,
        )
    max_output_tokens = int(node.config["max_output_tokens"])
    request_digest = canonical_digest(raw_request)
    route_digest = canonical_digest(model_route_document(route))
    agent = session.get(Agent, run.agent_id)
    workload = (
        session.get(WorkloadIdentity, agent.workload_identity_id) if agent is not None else None
    )
    if workload is None or not workload.active:
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "The workflow workload identity is unavailable.",
            status_code=409,
        )
    delegation = Delegation(
        id=uuid4(),
        organization_id=execution.organization_id,
        delegator_id=run.actor_id,
        delegator_type=run.actor_type,
        workload_identity_id=workload.id,
        action_types_json=[MODEL_ACTION_TYPE],
        valid_until=min(now + timedelta(minutes=30), execution.deadline_at),
        content_digest="pending",
        consumed_at=None,
    )
    delegation.content_digest = canonical_digest(
        {
            "organization_id": execution.organization_id,
            "delegation_id": delegation.id,
            "delegator_id": run.actor_id,
            "delegator_type": run.actor_type,
            "workload_identity_id": workload.id,
            "action_types": delegation.action_types_json,
            "workflow_execution_id": execution.id,
            "node_id": node.id,
            "sequence": execution.step_count,
            "valid_until": delegation.valid_until,
        }
    )
    session.add(delegation)
    session.flush()
    model_call_id = uuid4()
    expires_at = min(now + timedelta(seconds=node.timeout_seconds), execution.deadline_at)
    idempotency_key = f"workflow-model:{execution.id}:{node.id}:{execution.step_count}"
    content_digest = canonical_digest(
        {
            "organization_id": execution.organization_id,
            "model_call_id": model_call_id,
            "workflow_execution_id": execution.id,
            "run_id": execution.run_id,
            "model_route_id": route.id,
            "route_digest": route_digest,
            "delegation_id": delegation.id,
            "delegation_digest": delegation.content_digest,
            "policy_decision_id": policy_decision.id,
            "policy_decision_digest": policy_decision.content_digest,
            "node_id": node.id,
            "sequence": execution.step_count,
            "input_state_key": node.config["input_state_key"],
            "result_state_key": node.config["result_state_key"],
            "request_digest": request_digest,
            "max_output_tokens": max_output_tokens,
            "idempotency_key": idempotency_key,
            "expires_at": expires_at,
        }
    )
    call = ModelCall(
        id=model_call_id,
        organization_id=execution.organization_id,
        workflow_execution_id=execution.id,
        run_id=execution.run_id,
        model_route_id=route.id,
        delegation_id=delegation.id,
        policy_decision_id=policy_decision.id,
        node_id=node.id,
        sequence=execution.step_count,
        input_state_key=str(node.config["input_state_key"]),
        result_state_key=str(node.config["result_state_key"]),
        status="queued",
        request_digest=request_digest,
        route_digest=route_digest,
        content_digest=content_digest,
        encrypted_request="pending",
        output_digest=None,
        encrypted_output=None,
        provider_reference=None,
        idempotency_key=idempotency_key,
        max_output_tokens=max_output_tokens,
        input_tokens=None,
        output_tokens=None,
        cost_minor=None,
        worker_name=None,
        claim_token_hash=None,
        lease_expires_at=None,
        execute_attempts=0,
        reconcile_attempts=0,
        next_reconcile_at=None,
        expires_at=expires_at,
        completed_at=None,
        error_code=None,
    )
    call.encrypted_request = seal_json(
        raw_request,
        key=decode_aes256_key(settings.action_encryption_key_b64),
        associated_data=_model_call_aad(call, "request"),
    )
    session.add(call)
    session.flush()
    estimated_input_tokens = max(1, (len(canonical_bytes(raw_request)) + 3) // 4)
    reservations = reserve_budgets(
        session,
        context=BudgetContext(
            organization_id=execution.organization_id,
            project_id=run.project_id,
            environment_id=run.environment_id,
            agent_id=run.agent_id,
            actor_id=run.actor_id,
            actor_type=run.actor_type,
            model_route_id=route.id,
        ),
        run_id=run.id,
        estimates={
            "currency:USD": 1,
            "tokens": estimated_input_tokens + max_output_tokens,
            "requests": 1,
            "model_calls": 1,
        },
        now=now,
        ttl=max(timedelta(seconds=1), expires_at - now),
    )
    session.flush()
    link_model_call_reservations(
        session,
        organization_id=execution.organization_id,
        model_call_id=call.id,
        reservations=reservations,
    )
    session.add_all(
        [
            OutboxEvent(
                id=uuid4(),
                organization_id=execution.organization_id,
                topic="model.ready",
                aggregate_type="model_call",
                aggregate_id=call.id,
                deduplication_key=f"model.ready:{call.id}:1",
                payload_json={
                    "model_call_id": str(call.id),
                    "content_digest": call.content_digest,
                },
                available_at=now,
                attempts=0,
            ),
            OutboxEvent(
                id=uuid4(),
                organization_id=execution.organization_id,
                topic="workflow.ready",
                aggregate_type="workflow_execution",
                aggregate_id=execution.id,
                deduplication_key=f"workflow.model:{call.id}:timeout",
                payload_json={
                    "workflow_execution_id": str(execution.id),
                    "model_call_id": str(call.id),
                    "content_digest": call.content_digest,
                    "reason": "timeout",
                },
                available_at=expires_at,
                attempts=0,
            ),
        ]
    )
    execution.status = "waiting"
    execution.claim_token_hash = None
    execution.lease_expires_at = None
    run.status = "waiting"
    run.active_node = node.id
    current_event.processed_at = now
    _trace(
        session,
        organization_id=execution.organization_id,
        run_id=run.id,
        node_id=node.id,
        event_type="model.call_queued",
        status="queued",
        attributes={
            "model_call_id": str(call.id),
            "model_route_id": str(route.id),
            "delegation_id": str(delegation.id),
            "request_digest": request_digest,
            "route_digest": route_digest,
            "budget_reservation_ids": [str(row.id) for row in reservations],
            "raw_content_captured": False,
        },
    )
    _audit(
        session,
        organization_id=execution.organization_id,
        actor_id=run.actor_id,
        event_type="model.call_queued",
        subject_type="model_call",
        subject_id=call.id,
        content_digest=call.content_digest,
        metadata={
            "workflow_execution_id": str(execution.id),
            "model_route_id": str(route.id),
            "delegation_id": str(delegation.id),
            "request_digest": request_digest,
            "raw_content_captured": False,
        },
    )
    return call


def lock_model_timeout_event(session: Session, call_id: UUID) -> OutboxEvent | None:
    return session.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.deduplication_key == f"workflow.model:{call_id}:timeout")
        .with_for_update()
    )


def settle_model_call_and_wake(
    session: Session,
    *,
    call: ModelCall,
    now: datetime,
) -> None:
    if call.status not in {"completed", "failed", "timed_out"}:
        return
    session.add(
        OutboxEvent(
            id=uuid4(),
            organization_id=call.organization_id,
            topic="workflow.ready",
            aggregate_type="workflow_execution",
            aggregate_id=call.workflow_execution_id,
            deduplication_key=f"workflow.model:{call.id}:resume:{call.status}",
            payload_json={
                "workflow_execution_id": str(call.workflow_execution_id),
                "model_call_id": str(call.id),
                "content_digest": call.content_digest,
                "reason": "settled",
            },
            available_at=now,
            attempts=0,
        )
    )


def expire_model_call(
    session: Session,
    *,
    call_id: UUID,
    now: datetime,
) -> ModelCall:
    ready = session.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.aggregate_id == call_id, OutboxEvent.topic == "model.ready")
        .order_by(OutboxEvent.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    call = session.scalar(select(ModelCall).where(ModelCall.id == call_id).with_for_update())
    if call is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The model call disappeared before timeout settlement.",
            status_code=409,
        )
    if call.status in {"completed", "failed", "timed_out"}:
        return call
    if call.status in {"executing", "reconciling", "reconciliation_required"}:
        if call.status == "executing":
            call.status = "reconciliation_required"
            call.next_reconcile_at = now
        return call
    if call.status != "queued" or ready is None or ready.dispatched_at is not None:
        call.status = "reconciliation_required"
        call.next_reconcile_at = now
        return call
    ready.dispatched_at = now
    ready.processed_at = now
    ready.attempts += 1
    call.status = "timed_out"
    call.error_code = "model_call_timed_out_before_dispatch"
    call.completed_at = now
    settle_model_call_reservations(
        session,
        organization_id=call.organization_id,
        model_call_id=call.id,
        now=now,
        committed=False,
    )
    _trace(
        session,
        organization_id=call.organization_id,
        run_id=call.run_id,
        node_id=call.node_id,
        event_type="model.call_timed_out",
        status="timed_out",
        attributes={
            "model_call_id": str(call.id),
            "provider_request_started": False,
            "raw_content_captured": False,
        },
    )
    return call


def authorize_gateway_model_call(
    session: Session,
    *,
    model_call_id: UUID,
    content_digest: str,
    claim_token: str,
    mode: str,
) -> InternalModelAuthorizationResponse:
    call = session.get(ModelCall, model_call_id)
    if call is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Model call not found.", status_code=404)
    expected_status = "executing" if mode == "execute" else "reconciling"
    now = database_now(session)
    claim_hash = hashlib.sha256(claim_token.encode("utf-8")).hexdigest()
    if (
        call.status != expected_status
        or call.lease_expires_at is None
        or call.lease_expires_at <= now
        or call.claim_token_hash is None
        or not hmac.compare_digest(call.claim_token_hash, claim_hash)
        or not hmac.compare_digest(call.content_digest, content_digest)
    ):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "The model-call claim is stale, mismatched, or not executable.",
            status_code=409,
        )
    route = session.get(ModelRoute, call.model_route_id)
    decision = session.get(WorkflowPolicyDecision, call.policy_decision_id)
    delegation = session.get(Delegation, call.delegation_id)
    run = session.get(Run, call.run_id)
    reservations = model_call_reservations(
        session,
        organization_id=call.organization_id,
        model_call_id=call.id,
    )
    agent = session.get(Agent, run.agent_id) if run is not None else None
    workload = (
        session.get(WorkloadIdentity, agent.workload_identity_id) if agent is not None else None
    )
    if (
        route is None
        or route.status != "active"
        or route.provider != MODEL_PROVIDER
        or route.model != MODEL_NAME
        or not hmac.compare_digest(call.route_digest, canonical_digest(model_route_document(route)))
        or decision is None
        or decision.effect != DecisionEffect.ALLOW.value
        or delegation is None
        or MODEL_ACTION_TYPE not in delegation.action_types_json
        or run is None
        or workload is None
        or not workload.active
        or not reservations
        or any(row.status != "active" for row in reservations)
    ):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "The model-call governance lineage is incomplete or unavailable.",
            status_code=409,
        )
    if mode == "execute" and (
        decision.expires_at <= now
        or delegation.valid_until <= now
        or any(row.expires_at <= now for row in reservations)
    ):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "The model-call policy, delegation, or budget reservation has expired.",
            status_code=409,
        )
    return InternalModelAuthorizationResponse(
        organization_id=call.organization_id,
        run_id=call.run_id,
        workload_subject=workload.subject,
        audience=MODEL_AUDIENCE,
        content_digest=call.content_digest,
        request_digest=call.request_digest,
        model_route_id=route.id,
        provider=route.provider,
        model=route.model,
        decision_id=decision.id,
        delegation_id=delegation.id,
        budget_reservation_ids=[row.id for row in reservations],
    )
