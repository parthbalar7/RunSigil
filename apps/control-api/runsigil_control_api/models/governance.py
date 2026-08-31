from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from runsigil_control_api.models.base import Base, IdMixin, TenantMixin, TimestampMixin


class PolicyBundle(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "policy_bundles"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
        ),
    )

    project_id: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    document_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_digest: Mapped[str] = mapped_column(String(71))


class PolicyDecisionRecord(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "policy_decisions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "policy_bundle_id"],
            ["policy_bundles.organization_id", "policy_bundles.id"],
            ondelete="RESTRICT",
        ),
    )

    policy_bundle_id: Mapped[UUID] = mapped_column()
    effect: Mapped[str] = mapped_column(String(40))
    reason_code: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(500))
    input_digest: Mapped[str] = mapped_column(String(71))
    policy_digest: Mapped[str] = mapped_column(String(71))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BudgetScope(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "budget_scopes"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
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
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "model_route_id"],
            ["model_routes.organization_id", "model_routes.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(scope_type = 'organization' AND project_id IS NULL AND environment_id IS NULL "
            "AND agent_id IS NULL AND user_id IS NULL AND model_route_id IS NULL) OR "
            "(scope_type = 'project' AND project_id IS NOT NULL AND environment_id IS NULL "
            "AND agent_id IS NULL AND user_id IS NULL AND model_route_id IS NULL) OR "
            "(scope_type = 'environment' AND project_id IS NULL AND environment_id IS NOT NULL "
            "AND agent_id IS NULL AND user_id IS NULL AND model_route_id IS NULL) OR "
            "(scope_type = 'agent' AND project_id IS NULL AND environment_id IS NULL "
            "AND agent_id IS NOT NULL AND user_id IS NULL AND model_route_id IS NULL) OR "
            "(scope_type = 'user' AND project_id IS NULL AND environment_id IS NULL "
            "AND agent_id IS NULL AND user_id IS NOT NULL AND model_route_id IS NULL) OR "
            "(scope_type = 'model_route' AND project_id IS NULL AND environment_id IS NULL "
            "AND agent_id IS NULL AND user_id IS NULL AND model_route_id IS NOT NULL)",
            name="target_matches_type",
        ),
    )

    scope_type: Mapped[str] = mapped_column(String(30))
    project_id: Mapped[UUID | None] = mapped_column()
    environment_id: Mapped[UUID | None] = mapped_column()
    agent_id: Mapped[UUID | None] = mapped_column()
    user_id: Mapped[UUID | None] = mapped_column()
    model_route_id: Mapped[UUID | None] = mapped_column()


class Budget(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "budget_scope_id", "resource_key"),
        ForeignKeyConstraint(
            ["organization_id", "budget_scope_id"],
            ["budget_scopes.organization_id", "budget_scopes.id"],
            ondelete="RESTRICT",
        ),
    )

    budget_scope_id: Mapped[UUID] = mapped_column()
    resource_key: Mapped[str] = mapped_column(String(100))
    limit_value: Mapped[int] = mapped_column(BigInteger)
    reserved_value: Mapped[int] = mapped_column(BigInteger, default=0)
    spent_value: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class BudgetReservation(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "budget_reservations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "budget_id"],
            ["budgets.organization_id", "budgets.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
        ),
    )

    budget_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    resource_key: Mapped[str] = mapped_column(String(100))
    estimated_value: Mapped[int] = mapped_column(BigInteger)
    actual_value: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(30))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalRequest(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
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
    )

    run_id: Mapped[UUID] = mapped_column()
    intent_id: Mapped[UUID] = mapped_column()
    content_digest: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(30))
    risk: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(String(500))
    request_preview_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[UUID | None] = mapped_column()
    decision_reason: Mapped[str | None] = mapped_column(String(500))
