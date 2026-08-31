from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import JSON, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from runsigil_control_api.models.base import Base, IdMixin, TenantMixin, TimestampMixin


class EvidenceBundle(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "evidence_bundles"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "run_id"),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
        ),
    )

    run_id: Mapped[UUID] = mapped_column()
    content_digest: Mapped[str] = mapped_column(String(71))
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    signature_algorithm: Mapped[str] = mapped_column(String(30))
    signing_key_id: Mapped[str] = mapped_column(String(200))
    public_key_b64: Mapped[str] = mapped_column(Text)
    signature_b64: Mapped[str] = mapped_column(Text)
    export_status: Mapped[str] = mapped_column(String(30), default="local_only")
