from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKeyConstraint, String, UniqueConstraint
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


class Budget(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "project_id", "currency"),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
        ),
    )

    project_id: Mapped[UUID] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3))
    limit_minor: Mapped[int] = mapped_column(BigInteger)
    reserved_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    spent_minor: Mapped[int] = mapped_column(BigInteger, default=0)


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
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
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
