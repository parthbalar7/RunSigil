from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID

import httpx
from runsigil_contracts import ModelExecutionRequest, ModelExecutionResult, canonical_digest
from runsigil_control_api.models import ModelCall, OutboxEvent
from runsigil_control_api.services.budgets import settle_model_call_reservations
from runsigil_control_api.services.governed_actions import _audit, _trace, database_now
from runsigil_control_api.services.workflow_models import (
    decrypt_model_request,
    encrypt_model_output,
    lock_model_timeout_event,
    settle_model_call_and_wake,
)
from sqlalchemy import Engine, or_, select
from sqlalchemy.orm import Session


class ModelWorkerSettings(Protocol):
    worker_database_url: str
    gateway_url: str
    internal_service_token: str
    action_encryption_key_b64: str
    action_lease_seconds: int
    max_reconciliation_attempts: int
    reconciliation_delay_seconds: int


@dataclass(frozen=True)
class ClaimedModelCall:
    model_call_id: UUID
    organization_id: UUID
    run_id: UUID
    content_digest: str
    idempotency_key: str
    model: str
    max_output_tokens: int
    claim_token: str
    mode: str


class ModelCallWorker:
    def __init__(
        self,
        *,
        settings: ModelWorkerSettings,
        engine: Engine,
        worker_name: str,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.worker_name = worker_name

    def claim_ready(self) -> ClaimedModelCall | None:
        with Session(self.engine) as session, session.begin():
            now = database_now(session)
            event_id = session.scalar(
                select(OutboxEvent.id)
                .where(
                    OutboxEvent.topic == "model.ready",
                    OutboxEvent.dispatched_at.is_(None),
                    OutboxEvent.available_at <= now,
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(1)
            )
            if event_id is None:
                return None
            model_call_id = session.scalar(
                select(OutboxEvent.aggregate_id).where(OutboxEvent.id == event_id)
            )
            if model_call_id is None:
                return None
            lock_model_timeout_event(session, model_call_id)
            event = session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.topic == "model.ready",
                    OutboxEvent.dispatched_at.is_(None),
                    OutboxEvent.available_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
            if event is None:
                return None
            call = session.scalar(
                select(ModelCall).where(ModelCall.id == model_call_id).with_for_update()
            )
            if call is None or call.status != "queued":
                event.dispatched_at = now
                event.processed_at = now
                event.attempts += 1
                return None
            token = secrets.token_urlsafe(32)
            call.status = "executing"
            call.worker_name = self.worker_name
            call.claim_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            call.lease_expires_at = now + timedelta(seconds=self.settings.action_lease_seconds)
            call.execute_attempts += 1
            event.dispatched_at = now
            event.attempts += 1
            _trace(
                session,
                organization_id=call.organization_id,
                run_id=call.run_id,
                node_id=call.node_id,
                event_type="model.call_claimed",
                status="running",
                attributes={
                    "model_call_id": str(call.id),
                    "model_route_id": str(call.model_route_id),
                    "durable_claim_committed_before_provider": True,
                    "raw_content_captured": False,
                },
            )
            return ClaimedModelCall(
                model_call_id=call.id,
                organization_id=call.organization_id,
                run_id=call.run_id,
                content_digest=call.content_digest,
                idempotency_key=call.idempotency_key,
                model="demo-governed-model",
                max_output_tokens=call.max_output_tokens,
                claim_token=token,
                mode="execute",
            )

    def claim_reconciliation(self) -> ClaimedModelCall | None:
        with Session(self.engine) as session, session.begin():
            now = database_now(session)
            call_id = session.scalar(
                select(ModelCall.id)
                .where(
                    or_(
                        (ModelCall.status == "executing") & (ModelCall.lease_expires_at < now),
                        (ModelCall.status == "reconciliation_required")
                        & (
                            (ModelCall.next_reconcile_at.is_(None))
                            | (ModelCall.next_reconcile_at <= now)
                        ),
                    )
                )
                .order_by(ModelCall.updated_at, ModelCall.id)
                .limit(1)
            )
            if call_id is None:
                return None
            lock_model_timeout_event(session, call_id)
            call = session.scalar(
                select(ModelCall)
                .where(
                    ModelCall.id == call_id,
                    or_(
                        (ModelCall.status == "executing") & (ModelCall.lease_expires_at < now),
                        (ModelCall.status == "reconciliation_required")
                        & (
                            (ModelCall.next_reconcile_at.is_(None))
                            | (ModelCall.next_reconcile_at <= now)
                        ),
                    ),
                )
                .with_for_update(skip_locked=True)
            )
            if call is None:
                return None
            token = secrets.token_urlsafe(32)
            call.status = "reconciling"
            call.worker_name = self.worker_name
            call.claim_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            call.lease_expires_at = now + timedelta(seconds=self.settings.action_lease_seconds)
            call.reconcile_attempts += 1
            _trace(
                session,
                organization_id=call.organization_id,
                run_id=call.run_id,
                node_id=call.node_id,
                event_type="model.reconciliation_claimed",
                status="running",
                attributes={
                    "model_call_id": str(call.id),
                    "attempt": call.reconcile_attempts,
                    "raw_content_captured": False,
                },
            )
            return ClaimedModelCall(
                model_call_id=call.id,
                organization_id=call.organization_id,
                run_id=call.run_id,
                content_digest=call.content_digest,
                idempotency_key=call.idempotency_key,
                model="demo-governed-model",
                max_output_tokens=call.max_output_tokens,
                claim_token=token,
                mode="reconcile",
            )

    def _request(self, call_id: UUID) -> dict[str, Any]:
        with Session(self.engine) as session:
            call = session.get(ModelCall, call_id)
            if call is None:
                raise RuntimeError("claimed model call disappeared")
            return decrypt_model_request(call, self.settings)

    async def dispatch(self, claim: ClaimedModelCall) -> ModelExecutionResult:
        request = ModelExecutionRequest(
            model_call_id=claim.model_call_id,
            organization_id=claim.organization_id,
            run_id=claim.run_id,
            content_digest=claim.content_digest,
            idempotency_key=claim.idempotency_key,
            claim_token=claim.claim_token,
            model=claim.model,
            input=self._request(claim.model_call_id),
            max_output_tokens=claim.max_output_tokens,
        )
        path = "/v1/models/execute" if claim.mode == "execute" else "/v1/models/reconcile"
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                response = await client.post(
                    self.settings.gateway_url.rstrip("/") + path,
                    headers={"X-RunSigil-Service-Token": self.settings.internal_service_token},
                    json=request.model_dump(mode="json"),
                )
            if response.status_code >= 500:
                return ModelExecutionResult(
                    outcome="ambiguous", error_code="model_gateway_unavailable"
                )
            if response.status_code >= 400:
                return ModelExecutionResult(outcome="failed", error_code="model_gateway_denied")
            return ModelExecutionResult.model_validate(response.json())
        except (httpx.TimeoutException, httpx.NetworkError, ValueError):
            return ModelExecutionResult(
                outcome="ambiguous", error_code="model_gateway_outcome_unknown"
            )

    def settle(self, claim: ClaimedModelCall, result: ModelExecutionResult) -> None:
        with Session(self.engine) as session, session.begin():
            timeout_event = lock_model_timeout_event(session, claim.model_call_id)
            call = session.scalar(
                select(ModelCall).where(ModelCall.id == claim.model_call_id).with_for_update()
            )
            if call is None:
                return
            expected_status = "executing" if claim.mode == "execute" else "reconciling"
            expected_hash = hashlib.sha256(claim.claim_token.encode("utf-8")).hexdigest()
            if (
                call.status != expected_status
                or call.claim_token_hash is None
                or not hmac.compare_digest(call.claim_token_hash, expected_hash)
            ):
                return
            now = database_now(session)
            terminal = False
            if result.outcome == "completed":
                call.status = "completed"
                call.output_digest = canonical_digest(result.output)
                call.encrypted_output = encrypt_model_output(call, result.output, self.settings)
                call.provider_reference = result.provider_reference
                call.input_tokens = result.input_tokens
                call.output_tokens = result.output_tokens
                call.cost_minor = result.cost_minor
                call.completed_at = now
                call.error_code = None
                call.lease_expires_at = None
                settle_model_call_reservations(
                    session,
                    organization_id=call.organization_id,
                    model_call_id=call.id,
                    now=now,
                    committed=True,
                    actual_usage={
                        "currency:USD": result.cost_minor,
                        "tokens": result.input_tokens + result.output_tokens,
                        "requests": 1,
                        "model_calls": 1,
                    },
                )
                status = "completed"
                terminal = True
            elif result.outcome == "failed":
                call.status = "failed"
                call.error_code = result.error_code or "model_provider_failed"
                call.completed_at = now
                call.lease_expires_at = None
                settle_model_call_reservations(
                    session,
                    organization_id=call.organization_id,
                    model_call_id=call.id,
                    now=now,
                    committed=False,
                )
                status = "failed"
                terminal = True
            elif (
                claim.mode == "reconcile"
                and call.reconcile_attempts >= self.settings.max_reconciliation_attempts
            ):
                call.status = "failed"
                call.error_code = result.error_code or "model_outcome_unresolved"
                call.completed_at = now
                call.lease_expires_at = None
                settle_model_call_reservations(
                    session,
                    organization_id=call.organization_id,
                    model_call_id=call.id,
                    now=now,
                    committed=True,
                )
                status = "failed"
                terminal = True
            else:
                call.status = "reconciliation_required"
                call.error_code = result.error_code or "model_outcome_unknown"
                call.next_reconcile_at = now + timedelta(
                    seconds=self.settings.reconciliation_delay_seconds
                )
                call.lease_expires_at = None
                status = "ambiguous"
            ready_event = session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_id == call.id,
                    OutboxEvent.topic == "model.ready",
                )
                .order_by(OutboxEvent.created_at.desc())
                .limit(1)
                .with_for_update()
            )
            if terminal and ready_event is not None:
                ready_event.processed_at = now
            if terminal and timeout_event is not None and timeout_event.processed_at is None:
                timeout_event.processed_at = now
            _trace(
                session,
                organization_id=call.organization_id,
                run_id=call.run_id,
                node_id=call.node_id,
                event_type=f"model.call_{status}",
                status=status,
                attributes={
                    "model_call_id": str(call.id),
                    "model_route_id": str(call.model_route_id),
                    "output_digest": call.output_digest,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "cost_minor": call.cost_minor,
                    "raw_content_captured": False,
                },
            )
            _audit(
                session,
                organization_id=call.organization_id,
                actor_id=UUID("00000000-0000-4000-8000-000000000001"),
                event_type=f"model.call_{status}",
                subject_type="model_call",
                subject_id=call.id,
                content_digest=call.content_digest,
                metadata={
                    "workflow_execution_id": str(call.workflow_execution_id),
                    "mode": claim.mode,
                    "outcome": result.outcome,
                    "raw_content_captured": False,
                },
            )
            if terminal:
                settle_model_call_and_wake(session, call=call, now=now)
