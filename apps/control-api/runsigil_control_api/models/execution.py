from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from runsigil_control_api.models.base import Base, IdMixin, TenantMixin, TimestampMixin


class Run(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "environment_id"],
            ["environments.organization_id", "environments.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "agent_id"],
            ["agents.organization_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )

    project_id: Mapped[UUID] = mapped_column()
    environment_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column()
    actor_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    input_digest: Mapped[str] = mapped_column(String(71))
    active_node: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))


class Intent(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "intents"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "delegation_id"],
            ["delegations.organization_id", "delegations.id"],
            ondelete="RESTRICT",
        ),
    )

    run_id: Mapped[UUID] = mapped_column()
    actor_id: Mapped[UUID] = mapped_column()
    delegation_id: Mapped[UUID] = mapped_column()
    action_type: Mapped[str] = mapped_column(String(200))
    arguments_digest: Mapped[str] = mapped_column(String(71))
    content_digest: Mapped[str] = mapped_column(String(71))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))


class Action(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "intent_id"),
        UniqueConstraint("organization_id", "provider_idempotency_key"),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "intent_id"],
            ["intents.organization_id", "intents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "policy_decision_id"],
            ["policy_decisions.organization_id", "policy_decisions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "approval_request_id"],
            ["approval_requests.organization_id", "approval_requests.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "budget_reservation_id"],
            ["budget_reservations.organization_id", "budget_reservations.id"],
            ondelete="RESTRICT",
        ),
    )

    run_id: Mapped[UUID] = mapped_column()
    intent_id: Mapped[UUID] = mapped_column()
    policy_decision_id: Mapped[UUID] = mapped_column()
    approval_request_id: Mapped[UUID | None] = mapped_column()
    budget_reservation_id: Mapped[UUID] = mapped_column()
    tool_name: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_digest: Mapped[str] = mapped_column(String(71))
    encrypted_arguments: Mapped[str] = mapped_column(Text)
    request_preview_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    provider_idempotency_key: Mapped[str] = mapped_column(String(200))
    worker_name: Mapped[str | None] = mapped_column(String(200))
    claim_token_hash: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execute_attempts: Mapped[int] = mapped_column(Integer, default=0)
    reconcile_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_preview_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    provider_reference: Mapped[str | None] = mapped_column(String(300))
    error_code: Mapped[str | None] = mapped_column(String(100))


class OutboxEvent(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "deduplication_key"),
    )

    topic: Mapped[str] = mapped_column(String(100))
    aggregate_type: Mapped[str] = mapped_column(String(100))
    aggregate_id: Mapped[UUID] = mapped_column()
    deduplication_key: Mapped[str] = mapped_column(String(250))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class TraceEvent(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "trace_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
        ),
    )

    run_id: Mapped[UUID] = mapped_column()
    node_id: Mapped[str] = mapped_column(String(200))
    span_id: Mapped[str] = mapped_column(String(32))
    parent_span_id: Mapped[str | None] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    sequence: Mapped[int] = mapped_column(BigInteger)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class AuditEvent(Base, IdMixin, TenantMixin):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "sequence"),
    )

    sequence: Mapped[int] = mapped_column(BigInteger)
    actor_id: Mapped[UUID] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(100))
    subject_type: Mapped[str] = mapped_column(String(100))
    subject_id: Mapped[UUID] = mapped_column()
    content_digest: Mapped[str] = mapped_column(String(71))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    previous_hash: Mapped[str | None] = mapped_column(String(71))
    row_hash: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
