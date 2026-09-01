from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from runsigil_contracts import WorkflowDefinition, WorkflowNode, WorkflowNodeType, canonical_digest
from runsigil_contracts.errors import RunSigilError
from runsigil_evidence import EvidenceSigner
from runsigil_telemetry import Operation
from sqlalchemy import Engine, and_, func, or_, select
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Action,
    AuditEvent,
    EvaluationResult,
    EvidenceBundle,
    Intent,
    ModelCall,
    OutboxEvent,
    Run,
    RunCheckpoint,
    TraceEvent,
    WorkflowExecution,
    WorkflowNodeAttempt,
    WorkflowPolicyDecision,
    WorkflowReplay,
    WorkflowSubworkflowCall,
    WorkflowToolCall,
    WorkflowToolSimulationCall,
    WorkflowVersion,
    WorkflowWait,
)
from runsigil_control_api.services.evaluations import settle_evaluation_execution
from runsigil_control_api.services.governed_actions import _audit, _trace, database_now
from runsigil_control_api.services.workflow_models import (
    create_workflow_model_call,
    decrypt_model_output,
    expire_model_call,
)
from runsigil_control_api.services.workflow_policies import (
    evaluate_workflow_node_policy,
    require_executable_policy_effect,
)
from runsigil_control_api.services.workflow_replays import settle_workflow_replay
from runsigil_control_api.services.workflow_simulation import simulate_workflow_tool_call
from runsigil_control_api.services.workflow_subworkflows import (
    create_subworkflow_call,
    settle_parent_subworkflow_call,
)
from runsigil_control_api.services.workflow_tools import (
    TERMINAL_TOOL_CALL_STATUSES,
    create_workflow_tool_call,
    expire_workflow_tool_call,
    safe_tool_result,
    terminal_tool_call_evidence_digest,
)
from runsigil_control_api.services.workflow_waits import (
    WAIT_NODE_TYPES,
    create_workflow_wait,
    decrypt_wait_response,
    resolve_timer_wait,
    verify_wait_resolution,
)
from runsigil_control_api.services.workflows import (
    create_checkpoint,
    decrypt_execution_state,
    encrypt_execution_state,
)


class WorkflowWorkerSettings(Protocol):
    action_encryption_key_b64: str
    evidence_ed25519_private_key_b64: str
    evidence_signing_key_id: str
    action_lease_seconds: int
    approval_ttl_seconds: int


@dataclass(frozen=True)
class ClaimedWorkflowExecution:
    execution_id: UUID
    organization_id: UUID
    run_id: UUID
    outbox_event_id: UUID
    claim_token: str


TERMINAL_MODEL_CALL_STATUSES = frozenset({"completed", "failed", "timed_out"})


def _condition_value(state: dict[str, Any], field: str) -> Any:
    value: Any = state
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator == "eq":
        return bool(left == right)
    if operator == "ne":
        return bool(left != right)
    try:
        if operator == "gt":
            return bool(left > right)
        if operator == "gte":
            return bool(left >= right)
        if operator == "lt":
            return bool(left < right)
        if operator == "lte":
            return bool(left <= right)
    except TypeError:
        return False
    raise ValueError(f"unsupported condition operator: {operator}")


class WorkflowEngineWorker:
    def __init__(self, engine: Engine, settings: WorkflowWorkerSettings, worker_name: str) -> None:
        self.engine = engine
        self.settings = settings
        self.worker_name = worker_name

    def claim_ready(self) -> ClaimedWorkflowExecution | None:
        with Session(self.engine) as session, session.begin():
            now = database_now(session)
            recoverable = and_(
                WorkflowExecution.status == "running",
                WorkflowExecution.lease_expires_at < now,
            )
            event = session.scalar(
                select(OutboxEvent)
                .join(
                    WorkflowExecution,
                    WorkflowExecution.id == OutboxEvent.aggregate_id,
                )
                .where(
                    OutboxEvent.topic == "workflow.ready",
                    OutboxEvent.processed_at.is_(None),
                    OutboxEvent.available_at <= now,
                    or_(
                        WorkflowExecution.status.in_({"queued", "waiting"}),
                        recoverable,
                    ),
                    or_(OutboxEvent.dispatched_at.is_(None), recoverable),
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if event is None:
                return None
            execution = session.scalar(
                select(WorkflowExecution)
                .where(WorkflowExecution.id == event.aggregate_id)
                .with_for_update()
            )
            if execution is None or execution.status in {"completed", "failed", "cancelled"}:
                event.dispatched_at = now
                event.processed_at = now
                event.attempts += 1
                return None
            if execution.status == "running" and (
                execution.lease_expires_at is None or execution.lease_expires_at >= now
            ):
                return None
            if execution.status not in {"queued", "running", "waiting"}:
                event.dispatched_at = now
                event.processed_at = now
                event.attempts += 1
                return None
            token = secrets.token_urlsafe(32)
            execution.status = "running"
            execution.worker_name = self.worker_name
            execution.claim_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            execution.lease_expires_at = now + timedelta(seconds=self.settings.action_lease_seconds)
            execution.started_at = execution.started_at or now
            run = session.get(Run, execution.run_id)
            if run is None:
                raise RuntimeError("workflow execution run disappeared")
            run.status = "running"
            run.started_at = run.started_at or now
            event.dispatched_at = now
            event.attempts += 1
            return ClaimedWorkflowExecution(
                execution_id=execution.id,
                organization_id=execution.organization_id,
                run_id=execution.run_id,
                outbox_event_id=event.id,
                claim_token=token,
            )

    def _node_ready(
        self,
        node: WorkflowNode,
        definition: WorkflowDefinition,
        completed: set[str],
    ) -> bool:
        if node.type != WorkflowNodeType.JOIN:
            return True
        parents = {edge.source for edge in definition.edges if edge.target == node.id}
        return parents.issubset(completed)

    def _next_nodes(
        self,
        node: WorkflowNode,
        definition: WorkflowDefinition,
        state: dict[str, Any],
        loop_counts: dict[str, int],
        wait_resolution: str | None = None,
    ) -> list[str]:
        outgoing = [edge for edge in definition.edges if edge.source == node.id]
        if node.type == WorkflowNodeType.OUTPUT:
            return []
        if node.type == WorkflowNodeType.CONDITION:
            field = str(node.config["field"])
            operator = str(node.config["operator"])
            branch = (
                "true"
                if _compare(_condition_value(state, field), operator, node.config["value"])
                else "false"
            )
            return [next(edge.target for edge in outgoing if edge.branch == branch)]
        if node.type == WorkflowNodeType.PARALLEL:
            return sorted(edge.target for edge in outgoing)
        if node.type == WorkflowNodeType.BOUNDED_LOOP:
            count = loop_counts.get(node.id, 0)
            maximum = int(node.config["max_iterations"])
            if count < maximum:
                loop_counts[node.id] = count + 1
                branch = "continue"
            else:
                branch = "exit"
            return [next(edge.target for edge in outgoing if edge.branch == branch)]
        if node.type == WorkflowNodeType.APPROVAL:
            return [next(edge.target for edge in outgoing if edge.branch == wait_resolution)]
        return [outgoing[0].target]

    def _seal_workflow_evidence(self, session: Session, execution: WorkflowExecution) -> None:
        if (
            session.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == execution.run_id))
            is not None
        ):
            return
        run = session.get(Run, execution.run_id)
        version = session.get(WorkflowVersion, execution.workflow_version_id)
        if run is None or version is None:
            raise RuntimeError("cannot seal incomplete workflow lineage")
        attempts = list(
            session.scalars(
                select(WorkflowNodeAttempt)
                .where(WorkflowNodeAttempt.workflow_execution_id == execution.id)
                .order_by(WorkflowNodeAttempt.created_at, WorkflowNodeAttempt.id)
            )
        )
        checkpoints = list(
            session.scalars(
                select(RunCheckpoint)
                .where(RunCheckpoint.workflow_execution_id == execution.id)
                .order_by(RunCheckpoint.sequence, RunCheckpoint.created_at)
            )
        )
        waits = list(
            session.scalars(
                select(WorkflowWait)
                .where(WorkflowWait.workflow_execution_id == execution.id)
                .order_by(WorkflowWait.sequence, WorkflowWait.created_at, WorkflowWait.id)
            )
        )
        subworkflows = list(
            session.scalars(
                select(WorkflowSubworkflowCall)
                .where(WorkflowSubworkflowCall.parent_workflow_execution_id == execution.id)
                .order_by(
                    WorkflowSubworkflowCall.sequence,
                    WorkflowSubworkflowCall.created_at,
                    WorkflowSubworkflowCall.id,
                )
            )
        )
        tool_calls = list(
            session.scalars(
                select(WorkflowToolCall)
                .where(WorkflowToolCall.workflow_execution_id == execution.id)
                .order_by(
                    WorkflowToolCall.sequence,
                    WorkflowToolCall.created_at,
                    WorkflowToolCall.id,
                )
            )
        )
        tool_simulations = list(
            session.scalars(
                select(WorkflowToolSimulationCall)
                .where(WorkflowToolSimulationCall.workflow_execution_id == execution.id)
                .order_by(
                    WorkflowToolSimulationCall.sequence,
                    WorkflowToolSimulationCall.created_at,
                    WorkflowToolSimulationCall.id,
                )
            )
        )
        model_calls = list(
            session.scalars(
                select(ModelCall)
                .where(ModelCall.workflow_execution_id == execution.id)
                .order_by(ModelCall.sequence, ModelCall.created_at, ModelCall.id)
            )
        )
        policy_decisions = list(
            session.scalars(
                select(WorkflowPolicyDecision)
                .where(WorkflowPolicyDecision.workflow_execution_id == execution.id)
                .order_by(
                    WorkflowPolicyDecision.sequence,
                    WorkflowPolicyDecision.evaluation,
                    WorkflowPolicyDecision.id,
                )
            )
        )
        replay = session.scalar(
            select(WorkflowReplay).where(
                WorkflowReplay.replay_workflow_execution_id == execution.id
            )
        )
        traces = list(
            session.scalars(
                select(TraceEvent).where(TraceEvent.run_id == run.id).order_by(TraceEvent.sequence)
            )
        )
        audits = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.organization_id == execution.organization_id)
                .order_by(AuditEvent.sequence)
            )
        )
        evaluation_result = session.scalar(
            select(EvaluationResult).where(EvaluationResult.run_id == run.id)
        )
        manifest = {
            "schema": "runsigil.evidence/workflow-v1",
            "organization_id": str(execution.organization_id),
            "run": {
                "id": str(run.id),
                "kind": run.run_kind,
                "status": run.status,
                "input_digest": run.input_digest,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
            },
            "workflow": {
                "execution_id": str(execution.id),
                "workflow_version_id": str(version.id),
                "definition_digest": version.definition_digest,
                "execution_content_digest": execution.content_digest,
                "execution_mode": execution.execution_mode,
                "simulation_profile_id": (
                    str(execution.simulation_profile_id)
                    if execution.simulation_profile_id
                    else None
                ),
                "state_digest": execution.state_digest,
                "path_digest": canonical_digest(execution.path_json),
                "step_count": execution.step_count,
                "forked_from_checkpoint_id": (
                    str(execution.forked_from_checkpoint_id)
                    if execution.forked_from_checkpoint_id
                    else None
                ),
            },
            "node_attempts": [
                {
                    "id": str(row.id),
                    "node_id": row.node_id,
                    "node_type": row.node_type,
                    "attempt": row.attempt,
                    "status": row.status,
                    "input_digest": row.input_digest,
                    "output_digest": row.output_digest,
                    "error_code": row.error_code,
                }
                for row in attempts
            ],
            "checkpoints": [
                {
                    "id": str(row.id),
                    "sequence": row.sequence,
                    "node_id": row.node_id,
                    "state_digest": row.state_digest,
                    "content_digest": row.content_digest,
                    "parent_checkpoint_id": (
                        str(row.parent_checkpoint_id) if row.parent_checkpoint_id else None
                    ),
                }
                for row in checkpoints
            ],
            "waits": [
                {
                    "id": str(row.id),
                    "node_id": row.node_id,
                    "sequence": row.sequence,
                    "wait_type": row.wait_type,
                    "status": row.status,
                    "resolution": row.resolution,
                    "content_digest": row.content_digest,
                    "state_digest": row.state_digest,
                    "response_digest": row.response_digest,
                    "expires_at": row.expires_at,
                    "resolved_at": row.resolved_at,
                }
                for row in waits
            ],
            "subworkflows": [
                {
                    "id": str(row.id),
                    "node_id": row.node_id,
                    "sequence": row.sequence,
                    "deployment_id": str(row.deployment_id),
                    "child_workflow_execution_id": str(row.child_workflow_execution_id),
                    "child_run_id": str(row.child_run_id),
                    "result_state_key": row.result_state_key,
                    "status": row.status,
                    "input_state_digest": row.input_state_digest,
                    "child_execution_content_digest": row.child_execution_content_digest,
                    "result_state_digest": row.result_state_digest,
                    "content_digest": row.content_digest,
                    "expires_at": row.expires_at,
                    "resolved_at": row.resolved_at,
                }
                for row in subworkflows
            ],
            "tool_calls": [
                {
                    "id": str(row.id),
                    "node_id": row.node_id,
                    "sequence": row.sequence,
                    "tool_id": str(row.tool_id),
                    "child_run_id": str(row.child_run_id),
                    "action_id": str(row.action_id),
                    "intent_id": str(row.intent_id),
                    "status": row.status,
                    "arguments_digest": row.arguments_digest,
                    "tool_digest": row.tool_digest,
                    "action_content_digest": row.action_content_digest,
                    "result_digest": row.result_digest,
                    "content_digest": row.content_digest,
                    "child_evidence_digest": terminal_tool_call_evidence_digest(session, row),
                    "expires_at": row.expires_at,
                    "resolved_at": row.resolved_at,
                }
                for row in tool_calls
            ],
            "tool_simulations": [
                {
                    "id": str(row.id),
                    "node_id": row.node_id,
                    "sequence": row.sequence,
                    "simulation_profile_id": str(row.simulation_profile_id),
                    "tool_id": str(row.tool_id),
                    "status": row.status,
                    "arguments_digest": row.arguments_digest,
                    "tool_digest": row.tool_digest,
                    "profile_digest": row.profile_digest,
                    "result_digest": row.result_digest,
                    "content_digest": row.content_digest,
                    "completed_at": row.completed_at,
                    "side_effect_performed": False,
                }
                for row in tool_simulations
            ],
            "model_calls": [
                {
                    "id": str(row.id),
                    "node_id": row.node_id,
                    "sequence": row.sequence,
                    "model_route_id": str(row.model_route_id),
                    "delegation_id": str(row.delegation_id),
                    "policy_decision_id": str(row.policy_decision_id),
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
                    "error_code": row.error_code,
                    "expires_at": row.expires_at,
                    "completed_at": row.completed_at,
                    "raw_content_captured": False,
                }
                for row in model_calls
            ],
            "policy_decisions": [
                {
                    "id": str(row.id),
                    "node_id": row.node_id,
                    "sequence": row.sequence,
                    "evaluation": row.evaluation,
                    "policy_bundle_id": str(row.policy_bundle_id),
                    "effect": row.effect,
                    "reason_code": row.reason_code,
                    "input_digest": row.input_digest,
                    "policy_digest": row.policy_digest,
                    "content_digest": row.content_digest,
                    "expires_at": row.expires_at,
                }
                for row in policy_decisions
            ],
            "replay": (
                {
                    "id": str(replay.id),
                    "source_workflow_execution_id": str(replay.source_workflow_execution_id),
                    "source_run_id": str(replay.source_run_id),
                    "source_checkpoint_id": str(replay.source_checkpoint_id),
                    "status": replay.status,
                    "source_state_digest": replay.source_state_digest,
                    "source_path_digest": replay.source_path_digest,
                    "replay_state_digest": replay.replay_state_digest,
                    "replay_path_digest": replay.replay_path_digest,
                    "content_digest": replay.content_digest,
                }
                if replay is not None
                else None
            ),
            "evaluation": (
                {
                    "evaluation_id": str(evaluation_result.evaluation_id),
                    "scenario_id": str(evaluation_result.scenario_id),
                    "score_milli": evaluation_result.score_milli,
                    "status": evaluation_result.status,
                    "output_digest": evaluation_result.output_digest,
                    "trajectory_digest": evaluation_result.trajectory_digest,
                    "policy_outcome": evaluation_result.policy_outcome,
                    "safety_outcome": evaluation_result.safety_outcome,
                    "simulation_profile_id": (
                        str(execution.simulation_profile_id)
                        if execution.simulation_profile_id
                        else None
                    ),
                }
                if evaluation_result is not None
                else None
            ),
            "trace": [
                {
                    "id": str(event.id),
                    "sequence": event.sequence,
                    "node_id": event.node_id,
                    "event_type": event.event_type,
                    "status": event.status,
                    "attributes_digest": canonical_digest(event.attributes_json),
                }
                for event in traces
            ],
            "audit_segment": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "subject_id": str(event.subject_id),
                    "previous_hash": event.previous_hash,
                    "row_hash": event.row_hash,
                }
                for event in audits
            ],
            "privacy": {"raw_content_captured": False, "secret_values_included": False},
        }
        signer = EvidenceSigner(
            self.settings.evidence_ed25519_private_key_b64,
            self.settings.evidence_signing_key_id,
        )
        envelope = signer.sign(manifest)
        session.add(
            EvidenceBundle(
                id=uuid4(),
                organization_id=execution.organization_id,
                run_id=execution.run_id,
                content_digest=envelope.content_digest,
                manifest_json=envelope.manifest,
                signature_algorithm=envelope.signature_algorithm,
                signing_key_id=envelope.signing_key_id,
                public_key_b64=envelope.public_key_b64,
                signature_b64=envelope.signature_b64,
                export_status="local_only",
            )
        )

    def _fail(
        self,
        session: Session,
        *,
        execution: WorkflowExecution,
        run: Run,
        event: OutboxEvent,
        state: dict[str, Any],
        now: datetime,
        error_code: str,
    ) -> None:
        pending_waits = list(
            session.scalars(
                select(WorkflowWait)
                .where(
                    WorkflowWait.workflow_execution_id == execution.id,
                    WorkflowWait.status == "pending",
                )
                .with_for_update()
            )
        )
        for wait in pending_waits:
            wait.status = "cancelled"
            wait.resolution = "cancelled"
            wait.resolved_at = now
        pending_calls = list(
            session.scalars(
                select(WorkflowSubworkflowCall)
                .where(
                    WorkflowSubworkflowCall.parent_workflow_execution_id == execution.id,
                    WorkflowSubworkflowCall.status == "pending",
                )
                .with_for_update()
            )
        )
        for call in pending_calls:
            call.status = "cancelled"
            call.resolved_at = now
            session.add(
                OutboxEvent(
                    id=uuid4(),
                    organization_id=execution.organization_id,
                    topic="workflow.ready",
                    aggregate_type="workflow_execution",
                    aggregate_id=call.child_workflow_execution_id,
                    deduplication_key=f"subworkflow.call:{call.id}:parent-failed",
                    payload_json={
                        "workflow_execution_id": str(call.child_workflow_execution_id),
                        "parent_subworkflow_call_id": str(call.id),
                        "content_digest": call.content_digest,
                    },
                    available_at=now,
                    attempts=0,
                )
            )
        execution.status = "failed"
        execution.error_code = error_code
        execution.completed_at = now
        execution.claim_token_hash = None
        execution.lease_expires_at = None
        execution.version += 1
        run.status = "failed"
        run.error_code = error_code
        run.active_node = None
        run.completed_at = now
        event.processed_at = now
        _trace(
            session,
            organization_id=execution.organization_id,
            run_id=execution.run_id,
            node_id="workflow",
            event_type="workflow.failed",
            status="failed",
            attributes={"error_code": error_code, "raw_content_captured": False},
        )
        settle_evaluation_execution(
            session,
            execution=execution,
            state=state,
            failed_error=error_code,
            settings=self.settings,
        )
        settle_workflow_replay(session, execution=execution, now=now)
        settle_parent_subworkflow_call(
            session,
            child_execution=execution,
            child_run=run,
            now=now,
        )
        _audit(
            session,
            organization_id=execution.organization_id,
            actor_id=run.actor_id,
            event_type="workflow.failed",
            subject_type="run",
            subject_id=run.id,
            content_digest=execution.content_digest,
            metadata={"error_code": error_code, "step_count": execution.step_count},
        )
        self._seal_workflow_evidence(session, execution)

    def _cancel_claimed_execution(
        self,
        session: Session,
        *,
        execution: WorkflowExecution,
        run: Run,
        event: OutboxEvent,
        state: dict[str, Any],
        now: datetime,
        error_code: str,
    ) -> None:
        pending_waits = list(
            session.scalars(
                select(WorkflowWait)
                .where(
                    WorkflowWait.workflow_execution_id == execution.id,
                    WorkflowWait.status == "pending",
                )
                .with_for_update()
            )
        )
        for wait in pending_waits:
            wait.status = "cancelled"
            wait.resolution = "cancelled"
            wait.resolved_at = now
        pending_calls = list(
            session.scalars(
                select(WorkflowSubworkflowCall)
                .where(
                    WorkflowSubworkflowCall.parent_workflow_execution_id == execution.id,
                    WorkflowSubworkflowCall.status == "pending",
                )
                .with_for_update()
            )
        )
        for call in pending_calls:
            call.status = "cancelled"
            call.resolved_at = now
            session.add(
                OutboxEvent(
                    id=uuid4(),
                    organization_id=execution.organization_id,
                    topic="workflow.ready",
                    aggregate_type="workflow_execution",
                    aggregate_id=call.child_workflow_execution_id,
                    deduplication_key=f"subworkflow.call:{call.id}:ancestor-cancelled",
                    payload_json={
                        "workflow_execution_id": str(call.child_workflow_execution_id),
                        "parent_subworkflow_call_id": str(call.id),
                        "content_digest": call.content_digest,
                    },
                    available_at=now,
                    attempts=0,
                )
            )
        execution.status = "cancelled"
        execution.error_code = error_code
        execution.completed_at = now
        execution.claim_token_hash = None
        execution.lease_expires_at = None
        execution.version += 1
        run.status = "cancelled"
        run.error_code = error_code
        run.active_node = None
        run.completed_at = now
        event.processed_at = now
        _trace(
            session,
            organization_id=execution.organization_id,
            run_id=execution.run_id,
            node_id="workflow",
            event_type="workflow.cancelled",
            status="cancelled",
            attributes={"error_code": error_code, "raw_content_captured": False},
        )
        settle_evaluation_execution(
            session,
            execution=execution,
            state=state,
            failed_error=error_code,
            settings=self.settings,
        )
        settle_workflow_replay(session, execution=execution, now=now)
        settle_parent_subworkflow_call(
            session,
            child_execution=execution,
            child_run=run,
            now=now,
        )
        _audit(
            session,
            organization_id=execution.organization_id,
            actor_id=run.actor_id,
            event_type="workflow.cancelled",
            subject_type="run",
            subject_id=run.id,
            content_digest=execution.content_digest,
            metadata={"error_code": error_code, "side_effect_started": False},
        )
        self._seal_workflow_evidence(session, execution)

    def finalize_cancelled_once(self) -> bool:
        with Session(self.engine) as session, session.begin():
            now = database_now(session)
            event = session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.topic == "workflow.finalize",
                    OutboxEvent.processed_at.is_(None),
                    OutboxEvent.available_at <= now,
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if event is None:
                return False
            execution = session.scalar(
                select(WorkflowExecution)
                .where(WorkflowExecution.id == event.aggregate_id)
                .with_for_update()
            )
            if execution is None:
                event.processed_at = now
                event.attempts += 1
                return True
            if execution.status != "cancelled":
                event.processed_at = now
                event.attempts += 1
                return True
            state = decrypt_execution_state(execution, self.settings)
            run = session.get(Run, execution.run_id)
            if run is None:
                raise RuntimeError("cancelled workflow run disappeared")
            settle_evaluation_execution(
                session,
                execution=execution,
                state=state,
                failed_error=execution.error_code or "workflow_cancelled",
                settings=self.settings,
            )
            settle_workflow_replay(session, execution=execution, now=now)
            settle_parent_subworkflow_call(
                session,
                child_execution=execution,
                child_run=run,
                now=now,
            )
            self._seal_workflow_evidence(session, execution)
            event.dispatched_at = event.dispatched_at or now
            event.processed_at = now
            event.attempts += 1
            return True

    def advance(self, claim: ClaimedWorkflowExecution) -> None:
        with Session(self.engine) as session, session.begin():
            execution = session.scalar(
                select(WorkflowExecution)
                .where(WorkflowExecution.id == claim.execution_id)
                .with_for_update()
            )
            event = session.scalar(
                select(OutboxEvent).where(OutboxEvent.id == claim.outbox_event_id).with_for_update()
            )
            if execution is None or event is None or event.processed_at is not None:
                return
            expected_hash = hashlib.sha256(claim.claim_token.encode("utf-8")).hexdigest()
            if (
                execution.status != "running"
                or execution.claim_token_hash is None
                or not hmac.compare_digest(execution.claim_token_hash, expected_hash)
            ):
                return
            run = session.get(Run, execution.run_id)
            version = session.get(WorkflowVersion, execution.workflow_version_id)
            if run is None or version is None:
                raise RuntimeError("claimed workflow lineage disappeared")
            state = decrypt_execution_state(execution, self.settings)
            now = database_now(session)
            parent_call = session.scalar(
                select(WorkflowSubworkflowCall).where(
                    WorkflowSubworkflowCall.child_workflow_execution_id == execution.id
                )
            )
            if parent_call is not None and parent_call.status in {"cancelled", "timed_out"}:
                self._cancel_claimed_execution(
                    session,
                    execution=execution,
                    run=run,
                    event=event,
                    state=state,
                    now=now,
                    error_code="parent_subworkflow_cancelled",
                )
                return
            tool_boundary = session.scalar(
                select(WorkflowToolCall).where(
                    WorkflowToolCall.workflow_execution_id == execution.id,
                    WorkflowToolCall.sequence == execution.step_count,
                )
            )
            model_boundary = session.scalar(
                select(ModelCall).where(
                    ModelCall.workflow_execution_id == execution.id,
                    ModelCall.sequence == execution.step_count,
                )
            )
            if execution.deadline_at <= now and tool_boundary is None and model_boundary is None:
                self._fail(
                    session,
                    execution=execution,
                    run=run,
                    event=event,
                    state=state,
                    now=now,
                    error_code="workflow_deadline_exceeded",
                )
                return
            if execution.step_count >= execution.max_steps:
                self._fail(
                    session,
                    execution=execution,
                    run=run,
                    event=event,
                    state=state,
                    now=now,
                    error_code="workflow_step_limit_exceeded",
                )
                return
            definition = WorkflowDefinition.model_validate(version.definition_json)
            if canonical_digest(definition) != version.definition_digest:
                self._fail(
                    session,
                    execution=execution,
                    run=run,
                    event=event,
                    state=state,
                    now=now,
                    error_code="workflow_definition_digest_mismatch",
                )
                return
            nodes = {node.id: node for node in definition.nodes}
            completed = set(execution.completed_nodes_json)
            ready = [
                node_id
                for node_id in sorted(set(execution.current_nodes_json))
                if node_id in nodes and self._node_ready(nodes[node_id], definition, completed)
            ]
            if not ready:
                self._fail(
                    session,
                    execution=execution,
                    run=run,
                    event=event,
                    state=state,
                    now=now,
                    error_code="workflow_no_ready_node",
                )
                return
            wake_wait: WorkflowWait | None = None
            wake_call: WorkflowSubworkflowCall | None = None
            wake_tool: WorkflowToolCall | None = None
            wake_model: ModelCall | None = None
            wake_wait_value = event.payload_json.get("workflow_wait_id")
            wake_call_value = event.payload_json.get("workflow_subworkflow_call_id")
            wake_tool_value = event.payload_json.get("workflow_tool_call_id")
            wake_model_value = event.payload_json.get("model_call_id")
            if (
                sum(
                    value is not None
                    for value in (
                        wake_wait_value,
                        wake_call_value,
                        wake_tool_value,
                        wake_model_value,
                    )
                )
                > 1
            ):
                self._fail(
                    session,
                    execution=execution,
                    run=run,
                    event=event,
                    state=state,
                    now=now,
                    error_code="workflow_wake_payload_invalid",
                )
                return
            if wake_wait_value is not None:
                try:
                    wake_wait_id = UUID(str(wake_wait_value))
                except ValueError:
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_wait_wake_mismatch",
                    )
                    return
                wake_wait = session.scalar(
                    select(WorkflowWait).where(WorkflowWait.id == wake_wait_id).with_for_update()
                )
                if (
                    wake_wait is None
                    or wake_wait.workflow_execution_id != execution.id
                    or wake_wait.sequence != execution.step_count
                    or wake_wait.node_id not in ready
                ):
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_wait_wake_mismatch",
                    )
                    return
                node_id = wake_wait.node_id
            elif wake_call_value is not None:
                try:
                    wake_call_id = UUID(str(wake_call_value))
                except ValueError:
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_subworkflow_wake_mismatch",
                    )
                    return
                wake_call = session.scalar(
                    select(WorkflowSubworkflowCall)
                    .where(WorkflowSubworkflowCall.id == wake_call_id)
                    .with_for_update()
                )
                submitted_digest = event.payload_json.get("content_digest")
                if (
                    wake_call is None
                    or wake_call.parent_workflow_execution_id != execution.id
                    or wake_call.sequence != execution.step_count
                    or wake_call.node_id not in ready
                    or not isinstance(submitted_digest, str)
                    or not hmac.compare_digest(wake_call.content_digest, submitted_digest)
                ):
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_subworkflow_wake_mismatch",
                    )
                    return
                node_id = wake_call.node_id
            elif wake_tool_value is not None:
                try:
                    wake_tool_id = UUID(str(wake_tool_value))
                except ValueError:
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_tool_wake_mismatch",
                    )
                    return
                wake_tool = session.get(WorkflowToolCall, wake_tool_id)
                submitted_digest = event.payload_json.get("content_digest")
                if (
                    wake_tool is None
                    or wake_tool.workflow_execution_id != execution.id
                    or wake_tool.sequence != execution.step_count
                    or wake_tool.node_id not in ready
                    or not isinstance(submitted_digest, str)
                    or not hmac.compare_digest(wake_tool.content_digest, submitted_digest)
                ):
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_tool_wake_mismatch",
                    )
                    return
                node_id = wake_tool.node_id
            elif wake_model_value is not None:
                try:
                    wake_model_id = UUID(str(wake_model_value))
                except ValueError:
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_model_wake_mismatch",
                    )
                    return
                wake_model = session.scalar(
                    select(ModelCall).where(ModelCall.id == wake_model_id).with_for_update()
                )
                submitted_digest = event.payload_json.get("content_digest")
                if (
                    wake_model is None
                    or wake_model.workflow_execution_id != execution.id
                    or wake_model.sequence != execution.step_count
                    or wake_model.node_id not in ready
                    or not isinstance(submitted_digest, str)
                    or not hmac.compare_digest(
                        wake_model.content_digest,
                        submitted_digest,
                    )
                ):
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_model_wake_mismatch",
                    )
                    return
                node_id = wake_model.node_id
            else:
                node_id = ready[0]
            node = nodes[node_id]
            wait_resolution: str | None = None
            node_input_digest = execution.state_digest
            policy_decision = None
            if not (
                (node.type == WorkflowNodeType.TOOL and tool_boundary is not None)
                or (node.type == WorkflowNodeType.AGENT and model_boundary is not None)
            ):
                try:
                    policy_decision = evaluate_workflow_node_policy(
                        session,
                        execution=execution,
                        run=run,
                        node=node,
                        now=now,
                    )
                    require_executable_policy_effect(policy_decision)
                except RunSigilError as exc:
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code=exc.code.value,
                    )
                    return
            if node.type == WorkflowNodeType.TOOL:
                if execution.execution_mode == "simulation":
                    simulated_call = session.scalar(
                        select(WorkflowToolSimulationCall).where(
                            WorkflowToolSimulationCall.workflow_execution_id == execution.id,
                            WorkflowToolSimulationCall.node_id == node.id,
                            WorkflowToolSimulationCall.sequence == execution.step_count,
                        )
                    )
                    if simulated_call is not None:
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code="workflow_tool_simulation_duplicate",
                        )
                        return
                    try:
                        _simulated_call, simulated_result = simulate_workflow_tool_call(
                            session,
                            execution=execution,
                            run=run,
                            node=node,
                            state=state,
                            now=now,
                        )
                    except RunSigilError as exc:
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code=exc.code.value,
                        )
                        return
                    state[str(node.config["result_state_key"])] = simulated_result
                    execution.state_digest = canonical_digest(state)
                    execution.encrypted_state = encrypt_execution_state(
                        state,
                        execution,
                        self.settings,
                    )
                else:
                    call = wake_tool
                    if call is None:
                        call = session.scalar(
                            select(WorkflowToolCall)
                            .where(
                                WorkflowToolCall.workflow_execution_id == execution.id,
                                WorkflowToolCall.node_id == node.id,
                                WorkflowToolCall.sequence == execution.step_count,
                            )
                            .with_for_update()
                        )
                    if call is None:
                        try:
                            create_workflow_tool_call(
                                session,
                                execution=execution,
                                parent_run=run,
                                node=node,
                                state=state,
                                current_event=event,
                                now=now,
                                settings=self.settings,
                            )
                        except RunSigilError as exc:
                            self._fail(
                                session,
                                execution=execution,
                                run=run,
                                event=event,
                                state=state,
                                now=now,
                                error_code=exc.code.value,
                            )
                        return
                    if (
                        event.payload_json.get("reason") == "timeout"
                        and call.status not in TERMINAL_TOOL_CALL_STATUSES
                        and call.expires_at <= now
                    ):
                        try:
                            call = expire_workflow_tool_call(
                                session,
                                call_id=call.id,
                                now=now,
                            )
                        except RunSigilError as exc:
                            self._fail(
                                session,
                                execution=execution,
                                run=run,
                                event=event,
                                state=state,
                                now=now,
                                error_code=exc.code.value,
                            )
                            return
                    else:
                        call = session.scalar(
                            select(WorkflowToolCall)
                            .where(WorkflowToolCall.id == call.id)
                            .with_for_update()
                        )
                        if call is None:
                            self._fail(
                                session,
                                execution=execution,
                                run=run,
                                event=event,
                                state=state,
                                now=now,
                                error_code="workflow_tool_wake_mismatch",
                            )
                            return
                    if call.status not in TERMINAL_TOOL_CALL_STATUSES:
                        execution.status = "waiting"
                        execution.claim_token_hash = None
                        execution.lease_expires_at = None
                        run.status = "waiting"
                        run.active_node = node.id
                        event.processed_at = now
                        return
                    if call.status != "completed" or call.result_digest is None:
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code=f"workflow_tool_{call.status}",
                        )
                        return
                    action = session.get(Action, call.action_id)
                    intent = session.get(Intent, call.intent_id)
                    child_evidence = session.scalar(
                        select(EvidenceBundle).where(EvidenceBundle.run_id == call.child_run_id)
                    )
                    result = safe_tool_result(action) if action is not None else None
                    if (
                        action is None
                        or intent is None
                        or child_evidence is None
                        or action.run_id != call.child_run_id
                        or action.state != "committed"
                        or not hmac.compare_digest(
                            action.content_digest,
                            call.action_content_digest,
                        )
                        or not hmac.compare_digest(
                            intent.arguments_digest,
                            call.arguments_digest,
                        )
                        or result is None
                        or not hmac.compare_digest(
                            canonical_digest(result),
                            call.result_digest,
                        )
                    ):
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code="workflow_tool_result_invalid",
                        )
                        return
                    state[call.result_state_key] = result
                    execution.state_digest = canonical_digest(state)
                    execution.encrypted_state = encrypt_execution_state(
                        state,
                        execution,
                        self.settings,
                    )
            if node.type == WorkflowNodeType.AGENT:
                model_call = wake_model or model_boundary
                if model_call is None:
                    try:
                        create_workflow_model_call(
                            session,
                            execution=execution,
                            run=run,
                            node=node,
                            policy_decision=policy_decision,
                            state=state,
                            current_event=event,
                            now=now,
                            settings=self.settings,
                        )
                    except RunSigilError as exc:
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code=exc.code.value,
                        )
                    return
                if (
                    event.payload_json.get("reason") == "timeout"
                    and model_call.status not in TERMINAL_MODEL_CALL_STATUSES
                    and model_call.expires_at <= now
                ):
                    try:
                        model_call = expire_model_call(
                            session,
                            call_id=model_call.id,
                            now=now,
                        )
                    except RunSigilError as exc:
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code=exc.code.value,
                        )
                        return
                else:
                    model_call = session.scalar(
                        select(ModelCall).where(ModelCall.id == model_call.id).with_for_update()
                    )
                    if model_call is None:
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code="workflow_model_wake_mismatch",
                        )
                        return
                if model_call.status not in TERMINAL_MODEL_CALL_STATUSES:
                    execution.status = "waiting"
                    execution.claim_token_hash = None
                    execution.lease_expires_at = None
                    run.status = "waiting"
                    run.active_node = node.id
                    event.processed_at = now
                    return
                if model_call.status != "completed":
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code=f"workflow_model_{model_call.status}",
                    )
                    return
                try:
                    model_output = decrypt_model_output(model_call, self.settings)
                except RunSigilError as exc:
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code=exc.code.value,
                    )
                    return
                state[model_call.result_state_key] = model_output
                execution.state_digest = canonical_digest(state)
                execution.encrypted_state = encrypt_execution_state(
                    state,
                    execution,
                    self.settings,
                )
            if node.type == WorkflowNodeType.SUBWORKFLOW:
                subflow_call = wake_call or session.scalar(
                    select(WorkflowSubworkflowCall)
                    .where(
                        WorkflowSubworkflowCall.parent_workflow_execution_id == execution.id,
                        WorkflowSubworkflowCall.node_id == node.id,
                        WorkflowSubworkflowCall.sequence == execution.step_count,
                    )
                    .with_for_update()
                )
                if subflow_call is None:
                    try:
                        create_subworkflow_call(
                            session,
                            parent_execution=execution,
                            parent_run=run,
                            node=node,
                            state=state,
                            current_event=event,
                            now=now,
                            settings=self.settings,
                        )
                    except RunSigilError as exc:
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code=exc.code.value,
                        )
                    return
                if subflow_call.status == "pending":
                    if subflow_call.expires_at <= now:
                        subflow_call.status = "timed_out"
                        subflow_call.resolved_at = now
                        session.add(
                            OutboxEvent(
                                id=uuid4(),
                                organization_id=execution.organization_id,
                                topic="workflow.ready",
                                aggregate_type="workflow_execution",
                                aggregate_id=subflow_call.child_workflow_execution_id,
                                deduplication_key=(
                                    f"subworkflow.call:{subflow_call.id}:timeout-child"
                                ),
                                payload_json={
                                    "workflow_execution_id": str(
                                        subflow_call.child_workflow_execution_id
                                    ),
                                    "parent_subworkflow_call_id": str(subflow_call.id),
                                    "content_digest": subflow_call.content_digest,
                                },
                                available_at=now,
                                attempts=0,
                            )
                        )
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code="workflow_subworkflow_timed_out",
                        )
                    else:
                        execution.status = "waiting"
                        execution.claim_token_hash = None
                        execution.lease_expires_at = None
                        run.status = "waiting"
                        event.processed_at = now
                    return
                if subflow_call.status != "completed":
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code=f"workflow_subworkflow_{subflow_call.status}",
                    )
                    return
                child_execution = session.get(
                    WorkflowExecution,
                    subflow_call.child_workflow_execution_id,
                )
                if (
                    child_execution is None
                    or child_execution.status != "completed"
                    or subflow_call.result_state_digest is None
                    or not hmac.compare_digest(
                        child_execution.content_digest,
                        subflow_call.child_execution_content_digest,
                    )
                    or not hmac.compare_digest(
                        child_execution.state_digest,
                        subflow_call.result_state_digest,
                    )
                ):
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_subworkflow_result_invalid",
                    )
                    return
                state[subflow_call.result_state_key] = decrypt_execution_state(
                    child_execution,
                    self.settings,
                )
                execution.state_digest = canonical_digest(state)
                execution.encrypted_state = encrypt_execution_state(
                    state,
                    execution,
                    self.settings,
                )
            if node.type in WAIT_NODE_TYPES:
                wait = wake_wait or session.scalar(
                    select(WorkflowWait)
                    .where(
                        WorkflowWait.workflow_execution_id == execution.id,
                        WorkflowWait.node_id == node.id,
                        WorkflowWait.sequence == execution.step_count,
                    )
                    .with_for_update()
                )
                if wait is None:
                    if node.type == WorkflowNodeType.TIMER and (
                        now + timedelta(seconds=int(node.config["delay_seconds"]))
                        >= execution.deadline_at
                    ):
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code="workflow_wait_exceeds_deadline",
                        )
                        return
                    create_workflow_wait(
                        session,
                        execution=execution,
                        run=run,
                        node=node,
                        current_event=event,
                        now=now,
                    )
                    return
                if wait.wait_type != node.type.value:
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_wait_wake_mismatch",
                    )
                    return
                if wait.status == "pending":
                    if (
                        node.type == WorkflowNodeType.TIMER
                        and wait.due_at is not None
                        and wait.due_at <= now
                    ):
                        resolve_timer_wait(wait, now=now)
                    elif wait.expires_at <= now:
                        wait.status = "expired"
                        wait.resolution = "expired"
                        wait.resolved_at = now
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code="workflow_wait_expired",
                        )
                        return
                    else:
                        execution.status = "waiting"
                        execution.claim_token_hash = None
                        execution.lease_expires_at = None
                        run.status = (
                            "waiting_for_approval"
                            if node.type == WorkflowNodeType.APPROVAL
                            else "waiting"
                        )
                        event.processed_at = now
                        return
                if wait.status != "resolved" or wait.resolution is None:
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_wait_resolution_invalid",
                    )
                    return
                wait_resolution = wait.resolution
                verify_wait_resolution(wait)
                if node.type == WorkflowNodeType.APPROVAL and wait_resolution not in {
                    "approved",
                    "denied",
                }:
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_wait_resolution_invalid",
                    )
                    return
                if node.type == WorkflowNodeType.TIMER and wait_resolution != "elapsed":
                    self._fail(
                        session,
                        execution=execution,
                        run=run,
                        event=event,
                        state=state,
                        now=now,
                        error_code="workflow_wait_resolution_invalid",
                    )
                    return
                if node.type in {
                    WorkflowNodeType.EVENT,
                    WorkflowNodeType.REQUEST_INFORMATION,
                }:
                    if wait_resolution != "received":
                        self._fail(
                            session,
                            execution=execution,
                            run=run,
                            event=event,
                            state=state,
                            now=now,
                            error_code="workflow_wait_resolution_invalid",
                        )
                        return
                    state[str(node.config["state_key"])] = decrypt_wait_response(
                        wait, self.settings
                    )
                    execution.state_digest = canonical_digest(state)
                    execution.encrypted_state = encrypt_execution_state(
                        state, execution, self.settings
                    )
            attempt_number = (
                session.scalar(
                    select(func.count())
                    .select_from(WorkflowNodeAttempt)
                    .where(
                        WorkflowNodeAttempt.workflow_execution_id == execution.id,
                        WorkflowNodeAttempt.node_id == node_id,
                    )
                )
                or 0
            ) + 1
            attempt = WorkflowNodeAttempt(
                id=uuid4(),
                organization_id=execution.organization_id,
                workflow_execution_id=execution.id,
                run_id=execution.run_id,
                node_id=node.id,
                node_type=node.type.value,
                attempt=attempt_number,
                status="running",
                input_digest=node_input_digest,
                output_digest=None,
                started_at=now,
                completed_at=None,
                error_code=None,
            )
            session.add(attempt)
            loop_counts = dict(execution.loop_counts_json)
            next_nodes = self._next_nodes(
                node,
                definition,
                state,
                loop_counts,
                wait_resolution,
            )
            current = [value for value in execution.current_nodes_json if value != node_id]
            execution.current_nodes_json = sorted(set(current + next_nodes))
            if node_id not in completed:
                execution.completed_nodes_json = execution.completed_nodes_json + [node_id]
            execution.path_json = execution.path_json + [node_id]
            execution.loop_counts_json = loop_counts
            execution.step_count += 1
            execution.version += 1
            execution.claim_token_hash = None
            execution.lease_expires_at = None
            attempt.status = "completed"
            attempt.output_digest = execution.state_digest
            attempt.completed_at = now
            _trace(
                session,
                organization_id=execution.organization_id,
                run_id=execution.run_id,
                node_id=node.id,
                event_type="workflow.node_completed",
                status="completed",
                attributes={
                    "workflow_execution_id": str(execution.id),
                    "node_type": node.type.value,
                    "attempt": attempt_number,
                    "input_digest": attempt.input_digest,
                    "output_digest": attempt.output_digest,
                    "raw_content_captured": False,
                },
            )
            create_checkpoint(
                session,
                execution=execution,
                state=state,
                node_id=node.id,
                settings=self.settings,
            )
            event.processed_at = now
            if not execution.current_nodes_json:
                execution.status = "completed"
                execution.completed_at = now
                run.status = "completed"
                run.active_node = None
                run.completed_at = now
                settle_evaluation_execution(
                    session,
                    execution=execution,
                    state=state,
                    settings=self.settings,
                )
                settle_workflow_replay(session, execution=execution, now=now)
                settle_parent_subworkflow_call(
                    session,
                    child_execution=execution,
                    child_run=run,
                    now=now,
                )
                _trace(
                    session,
                    organization_id=execution.organization_id,
                    run_id=execution.run_id,
                    node_id=node.id,
                    event_type="workflow.completed",
                    status="completed",
                    attributes={
                        "workflow_execution_id": str(execution.id),
                        "state_digest": execution.state_digest,
                        "path_digest": canonical_digest(execution.path_json),
                        "step_count": execution.step_count,
                        "raw_content_captured": False,
                    },
                )
                _audit(
                    session,
                    organization_id=execution.organization_id,
                    actor_id=run.actor_id,
                    event_type="workflow.completed",
                    subject_type="run",
                    subject_id=run.id,
                    content_digest=execution.content_digest,
                    metadata={
                        "workflow_execution_id": str(execution.id),
                        "state_digest": execution.state_digest,
                        "path_digest": canonical_digest(execution.path_json),
                        "step_count": execution.step_count,
                    },
                )
                self._seal_workflow_evidence(session, execution)
            else:
                execution.status = "queued"
                run.status = "queued"
                run.active_node = sorted(execution.current_nodes_json)[0]
                session.add(
                    OutboxEvent(
                        id=uuid4(),
                        organization_id=execution.organization_id,
                        topic="workflow.ready",
                        aggregate_type="workflow_execution",
                        aggregate_id=execution.id,
                        deduplication_key=f"workflow.ready:{execution.id}:{execution.version}",
                        payload_json={
                            "workflow_execution_id": str(execution.id),
                            "content_digest": execution.content_digest,
                        },
                        available_at=now,
                        attempts=0,
                    )
                )

    def process_once(self) -> bool:
        if self.finalize_cancelled_once():
            return True
        claim = self.claim_ready()
        if claim is None:
            return False
        with Operation(
            "runsigil.workflow.advance",
            metric_name="runsigil.workflow.node.duration",
            attributes={
                "runsigil.workflow.execution.id": str(claim.execution_id),
                "runsigil.run.id": str(claim.run_id),
                "runsigil.content_captured": False,
            },
        ):
            self.advance(claim)
        return True
