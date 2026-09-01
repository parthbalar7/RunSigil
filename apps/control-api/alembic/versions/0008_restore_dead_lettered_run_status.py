"""Preserve the Milestone 2 dead-lettered run state after adding workflow waits.

Revision ID: 0008_run_status
Revises: 0007_m3_waits
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op

revision = "0008_run_status"
down_revision = "0007_m3_waits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE runs DROP CONSTRAINT ck_runs_status")
    op.execute(
        "ALTER TABLE runs ADD CONSTRAINT ck_runs_status CHECK "
        "(status IN ('authorizing','waiting_for_approval','waiting','queued','running',"
        "'completed','failed','cancelled','reconciliation_required','dead_lettered'))"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0008 is intentionally irreversible because removing an existing run state is unsafe"
    )
