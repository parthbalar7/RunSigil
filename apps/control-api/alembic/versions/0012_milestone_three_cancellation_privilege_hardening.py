"""Restrict application-role workflow cancellation writes.

Revision ID: 0012_m3_cancel_acl
Revises: 0011_m3_runtime
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op

revision = "0012_m3_cancel_acl"
down_revision = "0011_m3_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE UPDATE ON workflow_executions FROM runsigil_app")
    op.execute(
        "GRANT UPDATE (status, version, error_code, completed_at, claim_token_hash, "
        "lease_expires_at, updated_at) ON workflow_executions TO runsigil_app"
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_app_subworkflow_cancellation_only() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF current_user = 'runsigil_app' AND NOT (
             OLD.status = 'pending' AND NEW.status = 'cancelled'
             AND NEW.result_state_digest IS NULL AND NEW.resolved_at IS NOT NULL) THEN
            RAISE EXCEPTION 'RunSigil app role may only cancel a pending subworkflow call';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_app_subworkflow_cancellation_only "
        "BEFORE UPDATE ON workflow_subworkflow_calls FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_app_subworkflow_cancellation_only()"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0012 is intentionally irreversible because it narrows cancellation privileges"
    )
