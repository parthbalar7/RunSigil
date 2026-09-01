from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from runsigil_contracts import ActionExecutionRequest, ActionExecutionResult, canonical_digest
from runsigil_control_api.models import (
    Action,
    ApprovalRequest,
    AuditEvent,
    DeadLetter,
    EvidenceBundle,
    Intent,
    OutboxEvent,
    PolicyDecisionRecord,
    Run,
    TraceEvent,
    WorkflowToolCall,
)
from runsigil_control_api.services.budgets import (
    action_reservations,
    settle_action_reservations,
)
from runsigil_control_api.services.governed_actions import (
    _audit,
    _trace,
    database_now,
    decrypt_action_arguments,
)
from runsigil_control_api.services.workflow_engine import WorkflowEngineWorker
from runsigil_control_api.services.workflow_tools import (
    lock_tool_timeout_event,
    settle_workflow_tool_call,
    tool_call_id_for_action,
)
from runsigil_evidence import EvidenceSigner
from sqlalchemy import Engine, create_engine, or_, select
from sqlalchemy.orm import Session

from runsigil_worker.model_calls import ModelCallWorker
from runsigil_worker.settings import WorkerSettings, get_worker_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedAction:
    action_id: UUID
    organization_id: UUID
    run_id: UUID
    content_digest: str
    idempotency_key: str
    claim_token: str
    mode: str


class ActionWorker:
    def __init__(
        self, settings: WorkerSettings | None = None, engine: Engine | None = None
    ) -> None:
        self.settings = settings or get_worker_settings()
        self.engine = engine or create_engine(self.settings.worker_database_url, pool_pre_ping=True)
        self.worker_name = f"runsigil-action-worker-{secrets.token_hex(6)}"
        self.workflow_worker = WorkflowEngineWorker(
            self.engine,
            self.settings,
            self.worker_name,
        )
        self.model_call_worker = ModelCallWorker(
            settings=self.settings,
            engine=self.engine,
            worker_name=self.worker_name,
        )

    def claim_ready(self) -> ClaimedAction | None:
        with Session(self.engine) as session, session.begin():
            now = database_now(session)
            event_id = session.scalar(
                select(OutboxEvent.id)
                .where(
                    OutboxEvent.topic == "action.ready",
                    OutboxEvent.dispatched_at.is_(None),
                    OutboxEvent.available_at <= now,
                )
                .order_by(OutboxEvent.created_at)
                .limit(1)
            )
            if event_id is None:
                return None
            action_id = session.scalar(
                select(OutboxEvent.aggregate_id).where(OutboxEvent.id == event_id)
            )
            if action_id is None:
                return None
            tool_call_id = tool_call_id_for_action(session, action_id)
            if tool_call_id is not None:
                lock_tool_timeout_event(session, tool_call_id)
            event = session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.topic == "action.ready",
                    OutboxEvent.dispatched_at.is_(None),
                    OutboxEvent.available_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
            if event is None:
                return None
            tool_call = (
                session.scalar(
                    select(WorkflowToolCall)
                    .where(WorkflowToolCall.id == tool_call_id)
                    .with_for_update()
                )
                if tool_call_id is not None
                else None
            )
            action = session.scalar(
                select(Action).where(Action.id == event.aggregate_id).with_for_update()
            )
            if action is None or action.state != "approved":
                event.dispatched_at = now
                event.processed_at = now
                event.attempts += 1
                return None
            claim_token = secrets.token_urlsafe(32)
            action.state = "executing"
            action.version += 1
            action.worker_name = self.worker_name
            action.claim_token_hash = hashlib.sha256(claim_token.encode("utf-8")).hexdigest()
            action.lease_expires_at = now + timedelta(seconds=self.settings.action_lease_seconds)
            action.execute_attempts += 1
            if tool_call is not None:
                tool_call.status = "executing"
            event.dispatched_at = now
            event.attempts += 1
            run = session.get(Run, action.run_id)
            if run is not None:
                run.status = "running"
                run.active_node = "action-dispatch"
                run.started_at = run.started_at or now
            _trace(
                session,
                organization_id=action.organization_id,
                run_id=action.run_id,
                node_id="action-dispatch",
                event_type="action.claimed",
                status="running",
                attributes={
                    "action_id": str(action.id),
                    "worker": self.worker_name,
                    "content_digest": action.content_digest,
                    "durable_claim_committed_before_effect": True,
                },
            )
            return ClaimedAction(
                action_id=action.id,
                organization_id=action.organization_id,
                run_id=action.run_id,
                content_digest=action.content_digest,
                idempotency_key=action.provider_idempotency_key,
                claim_token=claim_token,
                mode="execute",
            )

    def claim_reconciliation(self) -> ClaimedAction | None:
        with Session(self.engine) as session, session.begin():
            now = database_now(session)
            action_id = session.scalar(
                select(Action.id)
                .where(
                    or_(
                        (Action.state == "executing") & (Action.lease_expires_at < now),
                        (Action.state == "reconciliation_required")
                        & (
                            (Action.next_reconcile_at.is_(None)) | (Action.next_reconcile_at <= now)
                        ),
                    )
                )
                .order_by(Action.updated_at)
                .limit(1)
            )
            if action_id is None:
                return None
            tool_call_id = tool_call_id_for_action(session, action_id)
            if tool_call_id is not None:
                lock_tool_timeout_event(session, tool_call_id)
            tool_call = (
                session.scalar(
                    select(WorkflowToolCall)
                    .where(WorkflowToolCall.id == tool_call_id)
                    .with_for_update()
                )
                if tool_call_id is not None
                else None
            )
            action = session.scalar(
                select(Action)
                .where(
                    Action.id == action_id,
                    or_(
                        (Action.state == "executing") & (Action.lease_expires_at < now),
                        (Action.state == "reconciliation_required")
                        & (
                            (Action.next_reconcile_at.is_(None)) | (Action.next_reconcile_at <= now)
                        ),
                    ),
                )
                .with_for_update(skip_locked=True)
            )
            if action is None:
                return None
            claim_token = secrets.token_urlsafe(32)
            action.state = "reconciling"
            action.version += 1
            action.worker_name = self.worker_name
            action.claim_token_hash = hashlib.sha256(claim_token.encode("utf-8")).hexdigest()
            action.lease_expires_at = now + timedelta(seconds=self.settings.action_lease_seconds)
            action.reconcile_attempts += 1
            action.reconcile_cycle_attempts += 1
            if tool_call is not None:
                tool_call.status = "reconciling"
            _trace(
                session,
                organization_id=action.organization_id,
                run_id=action.run_id,
                node_id="action-reconciliation",
                event_type="action.reconciliation_claimed",
                status="running",
                attributes={
                    "action_id": str(action.id),
                    "attempt": action.reconcile_attempts,
                    "cycle_attempt": action.reconcile_cycle_attempts,
                },
            )
            return ClaimedAction(
                action_id=action.id,
                organization_id=action.organization_id,
                run_id=action.run_id,
                content_digest=action.content_digest,
                idempotency_key=action.provider_idempotency_key,
                claim_token=claim_token,
                mode="reconcile",
            )

    def _arguments(self, action_id: UUID) -> dict[str, Any]:
        with Session(self.engine) as session:
            action = session.get(Action, action_id)
            if action is None:
                raise RuntimeError("claimed action disappeared")
            return decrypt_action_arguments(action, self.settings)

    async def dispatch(self, claim: ClaimedAction) -> ActionExecutionResult:
        request = ActionExecutionRequest(
            action_id=claim.action_id,
            organization_id=claim.organization_id,
            run_id=claim.run_id,
            content_digest=claim.content_digest,
            idempotency_key=claim.idempotency_key,
            claim_token=claim.claim_token,
            arguments=self._arguments(claim.action_id),
        )
        path = "/v1/actions/execute" if claim.mode == "execute" else "/v1/actions/reconcile"
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                response = await client.post(
                    self.settings.gateway_url.rstrip("/") + path,
                    headers={"X-RunSigil-Service-Token": self.settings.internal_service_token},
                    json=request.model_dump(mode="json"),
                )
            if response.status_code >= 500:
                return ActionExecutionResult(outcome="ambiguous", error_code="gateway_unavailable")
            if response.status_code >= 400:
                return ActionExecutionResult(outcome="failed", error_code="gateway_denied")
            return ActionExecutionResult.model_validate(response.json())
        except (httpx.TimeoutException, httpx.NetworkError, ValueError):
            return ActionExecutionResult(outcome="ambiguous", error_code="gateway_outcome_unknown")

    def settle(self, claim: ClaimedAction, result: ActionExecutionResult) -> None:
        with Session(self.engine) as session, session.begin():
            tool_call_id = tool_call_id_for_action(session, claim.action_id)
            timeout_event = (
                lock_tool_timeout_event(session, tool_call_id) if tool_call_id is not None else None
            )
            tool_call = (
                session.scalar(
                    select(WorkflowToolCall)
                    .where(WorkflowToolCall.id == tool_call_id)
                    .with_for_update()
                )
                if tool_call_id is not None
                else None
            )
            action = session.scalar(
                select(Action).where(Action.id == claim.action_id).with_for_update()
            )
            if action is None:
                return
            expected_state = "executing" if claim.mode == "execute" else "reconciling"
            expected_claim_hash = hashlib.sha256(claim.claim_token.encode("utf-8")).hexdigest()
            if action.state != expected_state or action.claim_token_hash != expected_claim_hash:
                return
            now = database_now(session)
            run = session.get(Run, action.run_id)
            if result.outcome == "committed":
                action.state = "committed"
                action.receipt_preview_json = result.receipt_preview
                action.provider_reference = result.provider_reference
                action.error_code = None
                action.lease_expires_at = None
                if run is not None:
                    run.status = "completed"
                    run.active_node = None
                    run.completed_at = now
                settle_action_reservations(
                    session,
                    organization_id=action.organization_id,
                    action_id=action.id,
                    now=now,
                    committed=True,
                )
                status = "committed"
            elif result.outcome == "failed":
                action.state = "failed"
                action.error_code = result.error_code or "provider_failed"
                action.lease_expires_at = None
                if run is not None:
                    run.status = "failed"
                    run.active_node = None
                    run.error_code = action.error_code
                    run.completed_at = now
                settle_action_reservations(
                    session,
                    organization_id=action.organization_id,
                    action_id=action.id,
                    now=now,
                    committed=False,
                )
                status = "failed"
            else:
                action.error_code = result.error_code or "provider_outcome_ambiguous"
                action.lease_expires_at = None
                if (
                    claim.mode == "reconcile"
                    and action.reconcile_cycle_attempts >= self.settings.max_reconciliation_attempts
                ):
                    self._dead_letter(session, action, run, now)
                    status = "dead_lettered"
                else:
                    action.state = "reconciliation_required"
                    action.next_reconcile_at = now + timedelta(
                        seconds=self.settings.reconciliation_delay_seconds
                    )
                    if run is not None:
                        run.status = "reconciliation_required"
                        run.active_node = "action-reconciliation"
                        run.error_code = action.error_code
                    status = "ambiguous"
            action.version += 1
            outbox = session.scalar(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_id == action.id, OutboxEvent.topic == "action.ready")
                .order_by(OutboxEvent.created_at.desc())
                .limit(1)
                .with_for_update()
            )
            if outbox is not None and status in {"committed", "failed", "dead_lettered"}:
                outbox.processed_at = now
            _trace(
                session,
                organization_id=action.organization_id,
                run_id=action.run_id,
                node_id="action-dispatch" if claim.mode == "execute" else "action-reconciliation",
                event_type=f"action.{status}",
                status=status,
                attributes={
                    "action_id": str(action.id),
                    "content_digest": action.content_digest,
                    "provider_reference": result.provider_reference,
                    "raw_content_captured": False,
                },
            )
            _audit(
                session,
                organization_id=action.organization_id,
                actor_id=UUID("00000000-0000-4000-8000-000000000001"),
                event_type=f"action.{status}",
                subject_type="action",
                subject_id=action.id,
                content_digest=action.content_digest,
                metadata={
                    "run_id": str(action.run_id),
                    "worker": self.worker_name,
                    "outcome": result.outcome,
                    "raw_content_captured": False,
                },
            )
            if result.outcome in {"committed", "failed"}:
                dead_letter = session.scalar(
                    select(DeadLetter).where(DeadLetter.action_id == action.id).with_for_update()
                )
                if dead_letter is not None and dead_letter.status != "resolved":
                    dead_letter.status = "resolved"
                    dead_letter.resolved_at = now
                    dead_letter.version += 1
                self._create_evidence(session, action)
            if tool_call is not None:
                settle_workflow_tool_call(
                    session,
                    call=tool_call,
                    action=action,
                    now=now,
                )
                if (
                    timeout_event is not None
                    and timeout_event.processed_at is None
                    and tool_call.status in {"completed", "failed"}
                ):
                    timeout_event.processed_at = now

    def _dead_letter(
        self,
        session: Session,
        action: Action,
        run: Run | None,
        now: datetime,
    ) -> None:
        outbox = session.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.aggregate_id == action.id, OutboxEvent.topic == "action.ready")
            .order_by(OutboxEvent.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        dead_letter = session.scalar(
            select(DeadLetter).where(DeadLetter.action_id == action.id).with_for_update()
        )
        if dead_letter is None:
            dead_letter = DeadLetter(
                id=uuid4(),
                organization_id=action.organization_id,
                action_id=action.id,
                run_id=action.run_id,
                outbox_event_id=outbox.id if outbox is not None else None,
                source="action-reconciliation",
                reason_code=action.error_code or "provider_outcome_ambiguous",
                status="open",
                attempt_count=action.reconcile_attempts,
                redrive_count=0,
                max_redrives=self.settings.max_dlq_redrives,
                version=1,
                content_digest=action.content_digest,
                resolved_at=None,
            )
            session.add(dead_letter)
        else:
            dead_letter.status = "open"
            dead_letter.reason_code = action.error_code or "provider_outcome_ambiguous"
            dead_letter.attempt_count = action.reconcile_attempts
            dead_letter.version += 1
            dead_letter.resolved_at = None
        action.state = "dead_lettered"
        action.next_reconcile_at = None
        if run is not None:
            run.status = "dead_lettered"
            run.active_node = None
            run.error_code = action.error_code

    def _create_evidence(self, session: Session, action: Action) -> None:
        if (
            session.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == action.run_id))
            is not None
        ):
            return
        run = session.get(Run, action.run_id)
        intent = session.get(Intent, action.intent_id)
        decision = session.get(PolicyDecisionRecord, action.policy_decision_id)
        approval = (
            session.get(ApprovalRequest, action.approval_request_id)
            if action.approval_request_id
            else None
        )
        reservations = action_reservations(
            session,
            organization_id=action.organization_id,
            action_id=action.id,
        )
        traces = list(
            session.scalars(
                select(TraceEvent)
                .where(TraceEvent.run_id == action.run_id)
                .order_by(TraceEvent.sequence)
            )
        )
        audits = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.organization_id == action.organization_id)
                .order_by(AuditEvent.sequence)
            )
        )
        dead_letter = session.scalar(select(DeadLetter).where(DeadLetter.action_id == action.id))
        if run is None or intent is None or decision is None or not reservations:
            raise RuntimeError("cannot seal incomplete action lineage")
        manifest = {
            "schema": "runsigil.evidence/v1",
            "organization_id": str(action.organization_id),
            "run": {
                "id": str(run.id),
                "status": run.status,
                "input_digest": run.input_digest,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
            },
            "intent": {
                "id": str(intent.id),
                "action_type": intent.action_type,
                "arguments_digest": intent.arguments_digest,
                "content_digest": intent.content_digest,
                "delegation_id": str(intent.delegation_id),
            },
            "action": {
                "id": str(action.id),
                "state": action.state,
                "content_digest": action.content_digest,
                "provider_idempotency_key": action.provider_idempotency_key,
                "provider_reference": action.provider_reference,
                "execute_attempts": action.execute_attempts,
                "reconcile_attempts": action.reconcile_attempts,
            },
            "policy": {
                "decision_id": str(decision.id),
                "effect": decision.effect,
                "reason_code": decision.reason_code,
                "policy_digest": decision.policy_digest,
            },
            "approval": (
                {
                    "id": str(approval.id),
                    "status": approval.status,
                    "content_digest": approval.content_digest,
                    "decided_at": approval.decided_at,
                    "decided_by": str(approval.decided_by) if approval.decided_by else None,
                }
                if approval is not None
                else None
            ),
            "budgets": [
                {
                    "reservation_id": str(reservation.id),
                    "resource_key": reservation.resource_key,
                    "estimated_value": reservation.estimated_value,
                    "actual_value": reservation.actual_value,
                    "status": reservation.status,
                }
                for reservation in reservations
            ],
            "dead_letter": (
                {
                    "id": str(dead_letter.id),
                    "status": dead_letter.status,
                    "reason_code": dead_letter.reason_code,
                    "attempt_count": dead_letter.attempt_count,
                    "redrive_count": dead_letter.redrive_count,
                    "max_redrives": dead_letter.max_redrives,
                    "version": dead_letter.version,
                }
                if dead_letter is not None
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
                organization_id=action.organization_id,
                run_id=action.run_id,
                content_digest=envelope.content_digest,
                manifest_json=envelope.manifest,
                signature_algorithm=envelope.signature_algorithm,
                signing_key_id=envelope.signing_key_id,
                public_key_b64=envelope.public_key_b64,
                signature_b64=envelope.signature_b64,
                export_status="local_only",
            )
        )

    async def process_once(self) -> bool:
        claim = self.claim_ready() or self.claim_reconciliation()
        if claim is not None:
            result = await self.dispatch(claim)
            self.settle(claim, result)
            return True
        model_claim = (
            self.model_call_worker.claim_ready() or self.model_call_worker.claim_reconciliation()
        )
        if model_claim is not None:
            model_result = await self.model_call_worker.dispatch(model_claim)
            self.model_call_worker.settle(model_claim, model_result)
            return True
        return self.workflow_worker.process_once()

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                processed = await self.process_once()
            except Exception:
                logger.exception("RunSigil worker iteration failed; durable work will be retried")
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except TimeoutError:
                    pass
