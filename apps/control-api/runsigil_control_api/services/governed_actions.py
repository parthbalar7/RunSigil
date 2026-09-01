from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast
from uuid import UUID, uuid4

from runsigil_contracts import (
    DecisionEffect,
    PolicyContext,
    canonical_digest,
    canonical_json_value,
)
from runsigil_contracts.crypto import decode_aes256_key, open_json, seal_json
from runsigil_contracts.errors import ErrorCode, RunSigilError
from runsigil_policy import PolicyEvaluationError, evaluate
from runsigil_telemetry import Operation, current_trace_identifiers
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Action,
    Agent,
    AISystem,
    ApprovalRequest,
    AuditEvent,
    Delegation,
    Environment,
    EvidenceBundle,
    Intent,
    Organization,
    OutboxEvent,
    PolicyBundle,
    PolicyDecisionRecord,
    Project,
    Run,
    TraceEvent,
    WorkflowExecution,
    WorkflowToolCall,
    WorkloadIdentity,
)
from runsigil_control_api.schemas import (
    GovernedActionInput,
    InternalAuthorizationResponse,
)
from runsigil_control_api.services.budgets import (
    BudgetContext,
    action_reservations,
    link_action_reservations,
    release_action_reservations,
    reserve_budgets,
)
from runsigil_control_api.settings import Settings, get_settings

if TYPE_CHECKING:
    from runsigil_control_api.auth import AuthContext

ACTION_TYPE = "demo.invoice.send"
TOOL_NAME = "demo.invoice.send"
ESTIMATED_COST_MINOR = 1
ACTION_BUDGET_ESTIMATES = {
    "currency:USD": ESTIMATED_COST_MINOR,
    "requests": 1,
    "concurrent_runs": 1,
    "tool_actions": 1,
}


class ActionEncryptionSettings(Protocol):
    action_encryption_key_b64: str


class GovernedActionSettings(ActionEncryptionSettings, Protocol):
    approval_ttl_seconds: int


class ActionActorContext(Protocol):
    @property
    def organization_id(self) -> UUID: ...

    @property
    def actor_id(self) -> UUID: ...

    @property
    def actor_type(self) -> str: ...


def database_now(session: Session) -> datetime:
    now = session.scalar(select(func.current_timestamp()))
    if now is None:
        raise RuntimeError("database did not return a current timestamp")
    return now


def _redacted_recipient(recipient: str) -> str:
    local, separator, domain = recipient.partition("@")
    if not separator:
        return "[redacted]"
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


def _action_aad(action: Action) -> dict[str, str]:
    return _action_aad_values(action.organization_id, action.id, action.content_digest)


def _action_aad_values(
    organization_id: UUID, action_id: UUID, content_digest: str
) -> dict[str, str]:
    return {
        "organization_id": str(organization_id),
        "action_id": str(action_id),
        "content_digest": content_digest,
    }


def decrypt_action_arguments(
    action: Action, settings: ActionEncryptionSettings | None = None
) -> dict[str, Any]:
    resolved_settings = settings or get_settings()
    key = decode_aes256_key(resolved_settings.action_encryption_key_b64)
    value = open_json(action.encrypted_arguments, key=key, associated_data=_action_aad(action))
    if not isinstance(value, dict):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "The durable action payload is invalid.",
            status_code=409,
        )
    return value


def _trace(
    session: Session,
    *,
    organization_id: UUID,
    run_id: UUID,
    node_id: str,
    event_type: str,
    status: str,
    attributes: dict[str, Any],
) -> TraceEvent:
    latest_sequence = session.scalar(
        select(func.coalesce(func.max(TraceEvent.sequence), 0)).where(TraceEvent.run_id == run_id)
    )
    sequence = latest_sequence if latest_sequence is not None else 0
    trace_id, span_id, parent_span_id = current_trace_identifiers(run_id)
    event = TraceEvent(
        id=uuid4(),
        organization_id=organization_id,
        run_id=run_id,
        node_id=node_id,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        event_type=event_type,
        status=status,
        sequence=sequence + 1,
        attributes_json=canonical_json_value(attributes),
    )
    session.add(event)
    return event


def _audit(
    session: Session,
    *,
    organization_id: UUID,
    actor_id: UUID,
    event_type: str,
    subject_type: str,
    subject_id: UUID,
    content_digest: str,
    metadata: dict[str, Any],
) -> AuditEvent:
    # Sessions intentionally disable autoflush so request handlers control the
    # durable write boundary. Flush pending audit rows before allocating the
    # next tenant sequence when one transaction appends multiple events.
    session.flush()
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"runsigil-audit:{organization_id}"},
    )
    previous = session.scalar(
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id)
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    )
    sequence = (previous.sequence + 1) if previous is not None else 1
    created_at = database_now(session)
    previous_hash = previous.row_hash if previous is not None else None
    document = {
        "organization_id": str(organization_id),
        "sequence": sequence,
        "actor_id": str(actor_id),
        "event_type": event_type,
        "subject_type": subject_type,
        "subject_id": str(subject_id),
        "content_digest": content_digest,
        "metadata": metadata,
        "previous_hash": previous_hash,
        "created_at": created_at,
    }
    event = AuditEvent(
        id=uuid4(),
        organization_id=organization_id,
        sequence=sequence,
        actor_id=actor_id,
        event_type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        content_digest=content_digest,
        metadata_json=canonical_json_value(metadata),
        previous_hash=previous_hash,
        row_hash=canonical_digest(document),
        created_at=created_at,
    )
    session.add(event)
    return event


def _require_catalog(
    session: Session, request: GovernedActionInput
) -> tuple[Project, Environment, Agent]:
    project = session.get(Project, request.project_id)
    environment = session.get(Environment, request.environment_id)
    agent = session.get(Agent, request.agent_id)
    if project is None or environment is None or agent is None:
        raise RunSigilError(
            ErrorCode.NOT_FOUND, "Project, environment, or agent not found.", status_code=404
        )
    system = session.get(AISystem, agent.system_id)
    if system is None or system.project_id != project.id:
        raise RunSigilError(
            ErrorCode.NOT_FOUND, "Agent is not registered in this project.", status_code=404
        )
    return project, environment, agent


def create_governed_action(
    session: Session,
    *,
    context: ActionActorContext,
    request: GovernedActionInput,
    settings: GovernedActionSettings | None = None,
) -> Run:
    settings = settings or get_settings()
    project, environment, agent = _require_catalog(session, request)
    arguments = {
        "recipient": str(request.recipient),
        "amount_cents": request.amount_cents,
        "description": request.description,
        "simulate_outcome": request.simulate_outcome,
    }
    arguments_digest = canonical_digest(arguments)
    input_digest = canonical_digest(
        {
            "project_id": project.id,
            "environment_id": environment.id,
            "agent_id": agent.id,
            "action_type": ACTION_TYPE,
            "arguments_digest": arguments_digest,
        }
    )
    existing = session.scalar(select(Run).where(Run.idempotency_key == request.idempotency_key))
    if existing is not None:
        if existing.input_digest != input_digest:
            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "The idempotency key already belongs to different run content.",
                status_code=409,
            )
        return existing

    bundle = session.scalar(
        select(PolicyBundle)
        .where(PolicyBundle.project_id == project.id, PolicyBundle.status == "active")
        .order_by(PolicyBundle.created_at.desc())
        .limit(1)
    )
    raw_bundle = bundle.document_json if bundle is not None else None
    now = database_now(session)
    policy_context = PolicyContext(
        action_type=ACTION_TYPE,
        resource="tool:demo.invoice.send",
        environment=environment.environment_type,
        risk="high",
        data_classification="confidential",
        actor_type=cast(
            Literal["user", "service", "workload"],
            context.actor_type,
        ),
        amount_minor=request.amount_cents,
        occurred_at=now,
    )
    with Operation(
        "runsigil.policy.evaluate",
        metric_name="runsigil.policy.evaluation.duration",
        attributes={
            "runsigil.action.type": ACTION_TYPE,
            "runsigil.environment.type": environment.environment_type,
            "runsigil.content_captured": False,
        },
    ):
        decision = evaluate(raw_bundle, policy_context)
    if bundle is None or bundle.content_digest != decision.policy_digest:
        raise PolicyEvaluationError(
            ErrorCode.POLICY_UNAVAILABLE,
            "The active policy bundle digest does not match its content.",
            status_code=503,
        )
    if decision.effect == DecisionEffect.DENY:
        raise RunSigilError(ErrorCode.POLICY_DENIED, decision.reason, status_code=403)
    if decision.effect not in {DecisionEffect.ALLOW, DecisionEffect.REQUIRE_APPROVAL}:
        raise RunSigilError(
            ErrorCode.POLICY_DENIED,
            f"Decision {decision.effect.value} is not executable in this milestone.",
            status_code=403,
        )

    run = Run(
        id=uuid4(),
        organization_id=context.organization_id,
        project_id=project.id,
        environment_id=environment.id,
        agent_id=agent.id,
        actor_id=context.actor_id,
        actor_type=context.actor_type,
        status="authorizing",
        idempotency_key=request.idempotency_key,
        input_digest=input_digest,
        active_node="policy-check",
    )
    session.add(run)
    session.flush()

    delegation_document = {
        "delegator_id": context.actor_id,
        "delegator_type": context.actor_type,
        "workload_identity_id": agent.workload_identity_id,
        "action_types": [ACTION_TYPE],
        "run_id": run.id,
        "valid_until": now + timedelta(minutes=30),
    }
    delegation = Delegation(
        id=uuid4(),
        organization_id=context.organization_id,
        delegator_id=context.actor_id,
        delegator_type=context.actor_type,
        workload_identity_id=agent.workload_identity_id,
        action_types_json=[ACTION_TYPE],
        valid_until=now + timedelta(minutes=30),
        content_digest=canonical_digest(delegation_document),
    )
    session.add(delegation)
    session.flush()

    intent_content = {
        "organization_id": context.organization_id,
        "run_id": run.id,
        "project_id": project.id,
        "environment_id": environment.id,
        "agent_id": agent.id,
        "actor_id": context.actor_id,
        "delegation_id": delegation.id,
        "action_type": ACTION_TYPE,
        "arguments_digest": arguments_digest,
        "idempotency_key": request.idempotency_key,
    }
    content_digest = canonical_digest(intent_content)
    intent = Intent(
        id=uuid4(),
        organization_id=context.organization_id,
        run_id=run.id,
        actor_id=context.actor_id,
        delegation_id=delegation.id,
        action_type=ACTION_TYPE,
        arguments_digest=arguments_digest,
        content_digest=content_digest,
        idempotency_key=request.idempotency_key,
        status="authorized",
    )
    session.add(intent)
    session.flush()

    decision_record = PolicyDecisionRecord(
        id=uuid4(),
        organization_id=context.organization_id,
        policy_bundle_id=bundle.id,
        effect=decision.effect.value,
        reason_code=decision.reason_code,
        reason=decision.reason,
        input_digest=canonical_digest(policy_context),
        policy_digest=decision.policy_digest,
        expires_at=decision.expires_at,
    )
    session.add(decision_record)
    with Operation(
        "runsigil.budget.reserve",
        metric_name="runsigil.budget.reservation.duration",
        attributes={
            "runsigil.run.id": str(run.id),
            "runsigil.resource.count": len(ACTION_BUDGET_ESTIMATES),
        },
    ):
        reservations = reserve_budgets(
            session,
            context=BudgetContext(
                organization_id=context.organization_id,
                project_id=project.id,
                environment_id=environment.id,
                agent_id=agent.id,
                actor_id=context.actor_id,
                actor_type=context.actor_type,
            ),
            run_id=run.id,
            estimates=ACTION_BUDGET_ESTIMATES,
            now=now,
        )
    reservation = next(row for row in reservations if row.resource_key == "currency:USD")

    approval: ApprovalRequest | None = None
    if decision.effect == DecisionEffect.REQUIRE_APPROVAL:
        approval = ApprovalRequest(
            id=uuid4(),
            organization_id=context.organization_id,
            run_id=run.id,
            intent_id=intent.id,
            content_digest=content_digest,
            status="pending",
            risk="high",
            reason=decision.reason,
            request_preview_json={
                "tool": TOOL_NAME,
                "recipient": _redacted_recipient(str(request.recipient)),
                "amount_cents": request.amount_cents,
                "description": request.description,
                "binding": "exact-content",
            },
            expires_at=now + timedelta(seconds=settings.approval_ttl_seconds),
        )
        session.add(approval)

    # These records form the immutable authorization lineage. Flush them before
    # inserting the action so every database-level foreign key is present even
    # though this service intentionally avoids ORM relationship cascades.
    session.flush()

    action_id = uuid4()
    encrypted_arguments = seal_json(
        arguments,
        key=decode_aes256_key(settings.action_encryption_key_b64),
        associated_data=_action_aad_values(context.organization_id, action_id, content_digest),
    )
    action = Action(
        id=action_id,
        organization_id=context.organization_id,
        run_id=run.id,
        intent_id=intent.id,
        policy_decision_id=decision_record.id,
        approval_request_id=approval.id if approval else None,
        budget_reservation_id=reservation.id,
        tool_name=TOOL_NAME,
        state="proposed" if approval else "approved",
        version=1,
        content_digest=content_digest,
        encrypted_arguments=encrypted_arguments,
        request_preview_json=(
            approval.request_preview_json
            if approval
            else {
                "tool": TOOL_NAME,
                "recipient": _redacted_recipient(str(request.recipient)),
                "amount_cents": request.amount_cents,
                "description": request.description,
            }
        ),
        provider_idempotency_key=f"rsa_{run.id.hex}",
    )
    session.add(action)
    session.flush()
    link_action_reservations(
        session,
        organization_id=context.organization_id,
        action_id=action.id,
        reservations=reservations,
    )

    run.status = "waiting_for_approval" if approval else "queued"
    run.active_node = "human-approval" if approval else "action-dispatch"
    _trace(
        session,
        organization_id=context.organization_id,
        run_id=run.id,
        node_id="policy-check",
        event_type="guardrail.decision",
        status=decision.effect.value,
        attributes={
            "decision_id": str(decision_record.id),
            "effect": decision.effect.value,
            "reason_code": decision.reason_code,
            "policy_digest": decision.policy_digest,
            "content_digest": content_digest,
            "raw_content_captured": False,
        },
    )
    if approval:
        _trace(
            session,
            organization_id=context.organization_id,
            run_id=run.id,
            node_id="human-approval",
            event_type="approval.requested",
            status="waiting",
            attributes={
                "approval_id": str(approval.id),
                "content_digest": content_digest,
                "expires_at": approval.expires_at,
            },
        )
    else:
        session.add(
            OutboxEvent(
                id=uuid4(),
                organization_id=context.organization_id,
                topic="action.ready",
                aggregate_type="action",
                aggregate_id=action.id,
                deduplication_key=f"action.ready:{action.id}:1",
                payload_json={"action_id": str(action.id), "content_digest": content_digest},
                available_at=now,
                attempts=0,
            )
        )
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="intent.created",
        subject_type="run",
        subject_id=run.id,
        content_digest=content_digest,
        metadata={
            "decision": decision.effect.value,
            "approval_id": str(approval.id) if approval else None,
            "budget_reservation_id": str(reservation.id),
            "budget_reservation_ids": [str(row.id) for row in reservations],
            "raw_content_captured": False,
        },
    )
    return run


def decide_approval(
    session: Session,
    *,
    context: AuthContext,
    approval_id: UUID,
    submitted_digest: str,
    decision: str,
    reason: str,
) -> Run:
    candidate_action = session.scalar(
        select(Action).where(Action.approval_request_id == approval_id)
    )
    tool_call: WorkflowToolCall | None = None
    if candidate_action is not None:
        from runsigil_control_api.services.workflow_tools import lock_tool_timeout_event

        tool_call_id = session.scalar(
            select(WorkflowToolCall.id).where(WorkflowToolCall.action_id == candidate_action.id)
        )
        if tool_call_id is not None:
            lock_tool_timeout_event(session, tool_call_id)
            tool_call = session.scalar(
                select(WorkflowToolCall)
                .where(WorkflowToolCall.id == tool_call_id)
                .with_for_update()
            )
    approval = session.scalar(
        select(ApprovalRequest).where(ApprovalRequest.id == approval_id).with_for_update()
    )
    if approval is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Approval not found.", status_code=404)
    if approval.status != "pending":
        raise RunSigilError(
            ErrorCode.APPROVAL_REPLAYED,
            "This approval has already been decided and cannot be replayed.",
            status_code=409,
        )
    now = database_now(session)
    if approval.expires_at <= now:
        approval.status = "expired"
        raise RunSigilError(
            ErrorCode.APPROVAL_EXPIRED, "This approval has expired.", status_code=409
        )
    action = session.scalar(
        select(Action).where(Action.approval_request_id == approval.id).with_for_update()
    )
    intent = session.get(Intent, approval.intent_id)
    run = session.get(Run, approval.run_id)
    if action is None or intent is None or run is None:
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED, "Approval lineage is incomplete.", status_code=409
        )
    arguments = decrypt_action_arguments(action)
    current_arguments_digest = canonical_digest(arguments)
    if (
        not hmac.compare_digest(submitted_digest, approval.content_digest)
        or not hmac.compare_digest(action.content_digest, approval.content_digest)
        or not hmac.compare_digest(intent.content_digest, approval.content_digest)
        or not hmac.compare_digest(current_arguments_digest, intent.arguments_digest)
    ):
        raise RunSigilError(
            ErrorCode.APPROVAL_DIGEST_MISMATCH,
            "Approval is bound to exact action content; changed arguments require a new request.",
            status_code=409,
        )
    approval.decided_at = now
    approval.decided_by = context.actor_id
    approval.decision_reason = reason
    if decision == "deny":
        approval.status = "denied"
        action.state = "rejected"
        action.version += 1
        intent.status = "denied"
        run.status = "cancelled"
        run.completed_at = now
        run.active_node = None
        release_action_reservations(
            session,
            organization_id=context.organization_id,
            action_id=action.id,
            now=now,
        )
        status = "denied"
        if tool_call is not None:
            from runsigil_control_api.services.workflow_tools import (
                reject_workflow_tool_call,
            )

            reject_workflow_tool_call(
                session,
                call=tool_call,
                now=now,
                error_code="workflow_tool_approval_denied",
            )
    else:
        approval.status = "approved"
        action.state = "approved"
        action.version += 1
        run.status = "queued"
        run.active_node = "action-dispatch"
        session.add(
            OutboxEvent(
                id=uuid4(),
                organization_id=context.organization_id,
                topic="action.ready",
                aggregate_type="action",
                aggregate_id=action.id,
                deduplication_key=f"action.ready:{action.id}:{action.version}",
                payload_json={"action_id": str(action.id), "content_digest": action.content_digest},
                available_at=now,
                attempts=0,
            )
        )
        status = "approved"
        if tool_call is not None:
            if tool_call.status != "pending_approval":
                raise RunSigilError(
                    ErrorCode.INVALID_TRANSITION,
                    "The workflow tool call is no longer waiting for this approval.",
                    status_code=409,
                )
            tool_call.status = "queued"
    _trace(
        session,
        organization_id=context.organization_id,
        run_id=run.id,
        node_id="human-approval",
        event_type=f"approval.{status}",
        status=status,
        attributes={
            "approval_id": str(approval.id),
            "content_digest": approval.content_digest,
            "decided_by": str(context.actor_id),
        },
    )
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type=f"approval.{status}",
        subject_type="approval",
        subject_id=approval.id,
        content_digest=approval.content_digest,
        metadata={"run_id": str(run.id), "decision_reason": reason},
    )
    return run


def cancel_run(
    session: Session,
    *,
    context: AuthContext,
    run_id: UUID,
) -> Run:
    """Cancel only at a durable pre-effect boundary.

    Queued and running work is deliberately not cancelable here because an outbox
    claim or external effect could race the request. Those states require a later
    fenced cancellation protocol.
    """

    run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Run not found.", status_code=404)
    if run.run_kind == "workflow":
        from runsigil_control_api.services.workflows import cancel_workflow_run

        return cancel_workflow_run(session, context=context, run=run)
    if run.status != "waiting_for_approval":
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "Only a run waiting for approval can be cancelled safely.",
            status_code=409,
        )
    action = session.scalar(select(Action).where(Action.run_id == run.id).with_for_update())
    if action is None or action.state != "proposed" or action.approval_request_id is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The run is not at a cancelable approval boundary.",
            status_code=409,
        )
    approval = session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == action.approval_request_id)
        .with_for_update()
    )
    intent = session.get(Intent, action.intent_id)
    if approval is None or approval.status != "pending" or intent is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "Cancelable governance lineage is incomplete or stale.",
            status_code=409,
        )
    now = database_now(session)
    approval.status = "denied"
    approval.decided_at = now
    approval.decided_by = context.actor_id
    approval.decision_reason = "Run cancelled before approval by authenticated caller."
    action.state = "rejected"
    action.version += 1
    intent.status = "denied"
    reservations = release_action_reservations(
        session,
        organization_id=context.organization_id,
        action_id=action.id,
        now=now,
    )
    run.status = "cancelled"
    run.active_node = None
    run.completed_at = now
    _trace(
        session,
        organization_id=context.organization_id,
        run_id=run.id,
        node_id="human-approval",
        event_type="run.cancelled",
        status="cancelled",
        attributes={
            "approval_id": str(approval.id),
            "content_digest": action.content_digest,
            "side_effect_started": False,
        },
    )
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="run.cancelled",
        subject_type="run",
        subject_id=run.id,
        content_digest=action.content_digest,
        metadata={
            "approval_id": str(approval.id),
            "budget_reservation_ids": [str(row.id) for row in reservations],
            "side_effect_started": False,
        },
    )
    return run


def authorize_gateway_action(
    session: Session,
    *,
    action_id: UUID,
    content_digest: str,
    claim_token: str,
    mode: str,
    settings: Settings | None = None,
) -> InternalAuthorizationResponse:
    settings = settings or get_settings()
    action = session.scalar(select(Action).where(Action.id == action_id))
    if action is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Action not found.", status_code=404)
    allowed_state = "executing" if mode == "execute" else "reconciling"
    if action.state != allowed_state:
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "Action is not in an executable state.",
            status_code=409,
        )
    now = database_now(session)
    if action.lease_expires_at is None or action.lease_expires_at <= now:
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED, "Action worker lease is not live.", status_code=409
        )
    claim_hash = hashlib.sha256(claim_token.encode("utf-8")).hexdigest()
    if action.claim_token_hash is None or not hmac.compare_digest(
        action.claim_token_hash, claim_hash
    ):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "Action claim identity does not match.",
            status_code=409,
        )
    if not hmac.compare_digest(action.content_digest, content_digest):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "Action content digest does not match.",
            status_code=409,
        )
    intent = session.get(Intent, action.intent_id)
    decision = session.get(PolicyDecisionRecord, action.policy_decision_id)
    reservations = action_reservations(
        session,
        organization_id=action.organization_id,
        action_id=action.id,
    )
    run = session.get(Run, action.run_id)
    if (
        intent is None
        or decision is None
        or run is None
        or not reservations
        or action.budget_reservation_id not in {row.id for row in reservations}
    ):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "Action authorization lineage is incomplete.",
            status_code=409,
        )
    bundle = session.get(PolicyBundle, decision.policy_bundle_id)
    if bundle is None or bundle.content_digest != decision.policy_digest:
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "Action governance lineage is unavailable or inconsistent.",
            status_code=409,
        )
    if mode == "execute" and (
        decision.expires_at <= now
        or bundle.status != "active"
        or any(row.status != "active" or row.expires_at <= now for row in reservations)
    ):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "Action governance is stale or unavailable.",
            status_code=409,
        )
    if mode == "reconcile" and any(row.status != "active" for row in reservations):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "Ambiguous-effect budget lineage is no longer reserved.",
            status_code=409,
        )
    if decision.effect == DecisionEffect.REQUIRE_APPROVAL.value:
        approval = session.get(ApprovalRequest, action.approval_request_id)
        if (
            approval is None
            or approval.status != "approved"
            or approval.content_digest != action.content_digest
        ):
            raise RunSigilError(
                ErrorCode.ACTION_NOT_AUTHORIZED,
                "Exact-content approval is not valid.",
                status_code=409,
            )
    elif decision.effect != DecisionEffect.ALLOW.value:
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED, "Policy does not allow this action.", status_code=409
        )
    arguments = decrypt_action_arguments(action, settings)
    if canonical_digest(arguments) != intent.arguments_digest:
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED, "Durable action content was modified.", status_code=409
        )
    agent = session.get(Agent, run.agent_id)
    workload = (
        session.get(WorkloadIdentity, agent.workload_identity_id) if agent is not None else None
    )
    if workload is None or not workload.active:
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED, "Workload identity is unavailable.", status_code=409
        )
    return InternalAuthorizationResponse(
        organization_id=action.organization_id,
        run_id=action.run_id,
        workload_subject=workload.subject,
        audience=settings.demo_provider_audience,
        content_digest=action.content_digest,
        arguments_digest=intent.arguments_digest,
        decision_id=decision.id,
        approval_id=action.approval_request_id,
        budget_reservation_id=action.budget_reservation_id,
        budget_reservation_ids=[row.id for row in reservations],
    )


def run_detail(session: Session, run_id: UUID) -> dict[str, Any]:
    run = session.get(Run, run_id)
    if run is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Run not found.", status_code=404)
    action = session.scalar(select(Action).where(Action.run_id == run.id))
    approval = (
        session.get(ApprovalRequest, action.approval_request_id)
        if action is not None and action.approval_request_id is not None
        else None
    )
    traces = list(
        session.scalars(
            select(TraceEvent).where(TraceEvent.run_id == run.id).order_by(TraceEvent.sequence)
        )
    )
    evidence = session.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run.id))
    workflow_execution = session.scalar(
        select(WorkflowExecution).where(WorkflowExecution.run_id == run.id)
    )
    workflow_summary: dict[str, Any] | None = None
    if workflow_execution is not None:
        from runsigil_control_api.services.workflows import workflow_execution_summary

        workflow_summary = workflow_execution_summary(session, workflow_execution)
    return {
        "id": run.id,
        "status": run.status,
        "project_id": run.project_id,
        "environment_id": run.environment_id,
        "agent_id": run.agent_id,
        "active_node": run.active_node,
        "input_digest": run.input_digest,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error_code": run.error_code,
        "run_kind": run.run_kind,
        "action": (
            {
                "id": action.id,
                "tool_name": action.tool_name,
                "state": action.state,
                "content_digest": action.content_digest,
                "request_preview": action.request_preview_json,
                "receipt_preview": action.receipt_preview_json,
                "execute_attempts": action.execute_attempts,
                "reconcile_attempts": action.reconcile_attempts,
                "reconcile_cycle_attempts": action.reconcile_cycle_attempts,
                "error_code": action.error_code,
            }
            if action is not None
            else None
        ),
        "approval": (
            {
                "id": approval.id,
                "run_id": approval.run_id,
                "status": approval.status,
                "risk": approval.risk,
                "reason": approval.reason,
                "content_digest": approval.content_digest,
                "request_preview": approval.request_preview_json,
                "expires_at": approval.expires_at,
            }
            if approval is not None
            else None
        ),
        "workflow": workflow_summary,
        "trace_events": [
            {
                "id": event.id,
                "node_id": event.node_id,
                "trace_id": event.trace_id,
                "span_id": event.span_id,
                "event_type": event.event_type,
                "status": event.status,
                "sequence": event.sequence,
                "attributes": event.attributes_json,
                "created_at": event.created_at,
            }
            for event in traces
        ],
        "evidence_status": evidence.export_status if evidence is not None else "pending",
    }


def context_snapshot(session: Session, context: AuthContext) -> dict[str, Any]:
    organization = session.get(Organization, context.organization_id)
    if organization is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Organization not found.", status_code=404)
    projects = list(session.scalars(select(Project).order_by(Project.name)))
    environments = list(session.scalars(select(Environment).order_by(Environment.name)))
    systems = list(session.scalars(select(AISystem).order_by(AISystem.name)))
    agents = list(session.scalars(select(Agent).order_by(Agent.name)))
    return {
        "organization": {
            "id": organization.id,
            "name": organization.name,
            "slug": organization.slug,
        },
        "projects": [{"id": row.id, "name": row.name, "slug": row.slug} for row in projects],
        "environments": [
            {
                "id": row.id,
                "name": row.name,
                "slug": row.slug,
                "environment_type": row.environment_type,
                "protected": row.protected,
            }
            for row in environments
        ],
        "systems": [
            {
                "id": row.id,
                "project_id": row.project_id,
                "name": row.name,
                "risk_tier": row.risk_tier,
            }
            for row in systems
        ],
        "agents": [
            {"id": row.id, "system_id": row.system_id, "name": row.name, "framework": row.framework}
            for row in agents
        ],
    }
