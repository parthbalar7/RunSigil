"""Harden worker privileges required for durable nested execution.

Revision ID: 0011_m3_runtime
Revises: 0010_m3_policy_replay
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op

revision = "0011_m3_runtime"
down_revision = "0010_m3_policy_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Referenced children are created by the workflow worker in the same transaction
    # that persists their parent call. The worker already owns INSERT on runs,
    # checkpoints, outbox, trace, and audit records; this is the one additional write
    # required for the exact child execution row. Its existing worker RLS policy still
    # applies, and UPDATE remains constrained by the workflow lineage triggers.
    op.execute("GRANT INSERT ON workflow_executions TO runsigil_worker")


def downgrade() -> None:
    raise RuntimeError(
        "0011 is intentionally irreversible because nested executions may depend on it"
    )
