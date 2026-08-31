from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Boolean, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from runsigil_control_api.models.base import Base, IdMixin, TenantMixin, TimestampMixin


class Project(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "slug"),
    )

    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))


class Environment(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "slug"),
    )

    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    environment_type: Mapped[str] = mapped_column(String(30))
    protected: Mapped[bool] = mapped_column(Boolean, default=False)


class AISystem(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "ai_systems"
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
    owner: Mapped[str] = mapped_column(String(200))
    risk_tier: Mapped[str] = mapped_column(String(30))


class Agent(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "system_id"],
            ["ai_systems.organization_id", "ai_systems.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workload_identity_id"],
            ["workload_identities.organization_id", "workload_identities.id"],
            ondelete="RESTRICT",
        ),
    )

    system_id: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(String(200))
    framework: Mapped[str] = mapped_column(String(100))
    workload_identity_id: Mapped[UUID] = mapped_column()


class AgentVersion(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "agent_id", "version"),
        ForeignKeyConstraint(
            ["organization_id", "agent_id"],
            ["agents.organization_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )

    agent_id: Mapped[UUID] = mapped_column()
    version: Mapped[int] = mapped_column(Integer)
    config_digest: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(30))


class Tool(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "tools"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "name"),
    )

    name: Mapped[str] = mapped_column(String(200))
    effect_class: Mapped[str] = mapped_column(String(30))
    risk: Mapped[str] = mapped_column(String(30))
    connector: Mapped[str] = mapped_column(String(200))
    input_schema_json: Mapped[dict[str, Any]] = mapped_column(JSON)
