from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from runsigil_control_api.models.base import Base, IdMixin, TenantMixin, TimestampMixin


class Organization(Base, IdMixin, TimestampMixin):
    __tablename__ = "organizations"

    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))


class User(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("organization_id", "id"),)

    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ServiceIdentity(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "service_identities"
    __table_args__ = (UniqueConstraint("organization_id", "id"),)

    name: Mapped[str] = mapped_column(String(200))
    audience: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkloadIdentity(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "workload_identities"
    __table_args__ = (UniqueConstraint("organization_id", "id"),)

    name: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiKey(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("key_hash"),
    )

    name: Mapped[str] = mapped_column(String(200))
    key_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[UUID] = mapped_column()
    actor_type: Mapped[str] = mapped_column(String(30), default="user")
    scopes_json: Mapped[list[str]] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Delegation(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "delegations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "workload_identity_id"],
            ["workload_identities.organization_id", "workload_identities.id"],
            ondelete="RESTRICT",
        ),
    )

    delegator_id: Mapped[UUID] = mapped_column()
    delegator_type: Mapped[str] = mapped_column(String(30))
    workload_identity_id: Mapped[UUID] = mapped_column()
    action_types_json: Mapped[list[str]] = mapped_column(JSON)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_digest: Mapped[str] = mapped_column(String(71))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
