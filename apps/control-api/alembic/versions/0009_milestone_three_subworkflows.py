"""Add durable referenced subworkflows and cancelable suspension records.

Revision ID: 0009_m3_subflows
Revises: 0008_run_status
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009_m3_subflows"
down_revision = "0008_run_status"
branch_labels = None
depends_on = None


def _tenant_columns() -> list[sa.SchemaItem]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("organization_id", "id"),
    ]


def upgrade() -> None:
    op.create_table(
        "workflow_subworkflow_calls",
        *_tenant_columns(),
        sa.Column("parent_workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("child_workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("child_run_id", sa.Uuid(), nullable=False),
        sa.Column("result_state_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("input_state_digest", sa.String(length=71), nullable=False),
        sa.Column("child_execution_content_digest", sa.String(length=71), nullable=False),
        sa.Column("result_state_digest", sa.String(length=71), nullable=True),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id",
            "parent_workflow_execution_id",
            "node_id",
            "sequence",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "parent_workflow_execution_id", "parent_run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_subworkflow_call_parent_execution_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "child_workflow_execution_id", "child_run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_subworkflow_call_child_execution_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "deployment_id"],
            ["workflow_deployments.organization_id", "workflow_deployments.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('pending','completed','failed','cancelled','timed_out')",
            name="status",
        ),
    )
    op.create_index(
        "ix_workflow_subworkflow_calls_organization_id",
        "workflow_subworkflow_calls",
        ["organization_id"],
    )
    op.create_index(
        "ix_workflow_subworkflow_calls_child",
        "workflow_subworkflow_calls",
        ["organization_id", "child_workflow_execution_id", "status"],
    )

    op.execute("ALTER TABLE workflow_waits DROP CONSTRAINT ck_workflow_waits_status")
    op.execute("ALTER TABLE workflow_waits DROP CONSTRAINT ck_workflow_waits_resolution")
    op.execute(
        "ALTER TABLE workflow_waits ADD CONSTRAINT ck_workflow_waits_status "
        "CHECK (status IN ('pending','resolved','expired','cancelled'))"
    )
    op.execute(
        "ALTER TABLE workflow_waits ADD CONSTRAINT ck_workflow_waits_resolution "
        "CHECK (resolution IS NULL OR resolution IN "
        "('elapsed','approved','denied','received','expired','cancelled'))"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION runsigil_immutable_workflow_wait_lineage() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id
             OR NEW.workflow_execution_id <> OLD.workflow_execution_id
             OR NEW.run_id <> OLD.run_id OR NEW.node_id <> OLD.node_id
             OR NEW.sequence <> OLD.sequence OR NEW.wait_type <> OLD.wait_type
             OR NEW.content_digest <> OLD.content_digest
             OR NEW.state_digest <> OLD.state_digest
             OR NEW.request_metadata_json::jsonb IS DISTINCT FROM OLD.request_metadata_json::jsonb
             OR NEW.event_key IS DISTINCT FROM OLD.event_key
             OR NEW.due_at IS DISTINCT FROM OLD.due_at
             OR NEW.expires_at <> OLD.expires_at THEN
            RAISE EXCEPTION 'RunSigil workflow wait lineage is immutable';
          END IF;
          IF OLD.status <> 'pending' AND (
             NEW.status <> OLD.status OR NEW.resolution IS DISTINCT FROM OLD.resolution
             OR NEW.response_digest IS DISTINCT FROM OLD.response_digest
             OR NEW.encrypted_response IS DISTINCT FROM OLD.encrypted_response
             OR NEW.resolved_by IS DISTINCT FROM OLD.resolved_by
             OR NEW.resolved_at IS DISTINCT FROM OLD.resolved_at) THEN
            RAISE EXCEPTION 'RunSigil workflow wait resolution is single use';
          END IF;
          IF NEW.status = 'pending' AND (
             NEW.resolution IS NOT NULL OR NEW.response_digest IS NOT NULL
             OR NEW.encrypted_response IS NOT NULL OR NEW.resolved_by IS NOT NULL
             OR NEW.resolved_at IS NOT NULL) THEN
            RAISE EXCEPTION 'RunSigil pending workflow wait cannot contain a response';
          END IF;
          IF NEW.status = 'resolved' AND (
             NEW.resolution IS NULL OR NEW.response_digest IS NULL
             OR NEW.resolved_at IS NULL) THEN
            RAISE EXCEPTION 'RunSigil resolved workflow wait is incomplete';
          END IF;
          IF NEW.status = 'expired' AND (
             NEW.resolution <> 'expired' OR NEW.response_digest IS NOT NULL
             OR NEW.encrypted_response IS NOT NULL OR NEW.resolved_by IS NOT NULL
             OR NEW.resolved_at IS NULL) THEN
            RAISE EXCEPTION 'RunSigil expired workflow wait is invalid';
          END IF;
          IF NEW.status = 'cancelled' AND (
             NEW.resolution <> 'cancelled' OR NEW.response_digest IS NOT NULL
             OR NEW.encrypted_response IS NOT NULL OR NEW.resolved_by IS NOT NULL
             OR NEW.resolved_at IS NULL) THEN
            RAISE EXCEPTION 'RunSigil cancelled workflow wait is invalid';
          END IF;
          IF NEW.status = 'resolved' AND (
             (NEW.wait_type = 'timer' AND (
               NEW.resolution <> 'elapsed' OR NEW.resolved_by IS NOT NULL
               OR NEW.encrypted_response IS NOT NULL))
             OR (NEW.wait_type = 'approval' AND (
               NEW.resolution NOT IN ('approved','denied')
               OR NEW.resolved_by IS NULL OR NEW.encrypted_response IS NOT NULL))
             OR (NEW.wait_type IN ('event','request_information') AND (
               NEW.resolution <> 'received' OR NEW.resolved_by IS NULL
               OR NEW.encrypted_response IS NULL))) THEN
            RAISE EXCEPTION 'RunSigil workflow wait resolution does not match its type';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_subworkflow_call() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id
             OR NEW.parent_workflow_execution_id <> OLD.parent_workflow_execution_id
             OR NEW.parent_run_id <> OLD.parent_run_id OR NEW.node_id <> OLD.node_id
             OR NEW.sequence <> OLD.sequence OR NEW.deployment_id <> OLD.deployment_id
             OR NEW.child_workflow_execution_id <> OLD.child_workflow_execution_id
             OR NEW.child_run_id <> OLD.child_run_id
             OR NEW.result_state_key <> OLD.result_state_key
             OR NEW.input_state_digest <> OLD.input_state_digest
             OR NEW.child_execution_content_digest <> OLD.child_execution_content_digest
             OR NEW.content_digest <> OLD.content_digest OR NEW.expires_at <> OLD.expires_at THEN
            RAISE EXCEPTION 'RunSigil subworkflow call lineage is immutable';
          END IF;
          IF OLD.status <> 'pending' AND (
             NEW.status <> OLD.status
             OR NEW.result_state_digest IS DISTINCT FROM OLD.result_state_digest
             OR NEW.resolved_at IS DISTINCT FROM OLD.resolved_at) THEN
            RAISE EXCEPTION 'RunSigil subworkflow call settlement is single use';
          END IF;
          IF NEW.status = 'pending' AND (
             NEW.result_state_digest IS NOT NULL OR NEW.resolved_at IS NOT NULL) THEN
            RAISE EXCEPTION 'RunSigil pending subworkflow call cannot contain a result';
          END IF;
          IF NEW.status = 'completed' AND (
             NEW.result_state_digest IS NULL OR NEW.resolved_at IS NULL) THEN
            RAISE EXCEPTION 'RunSigil completed subworkflow call is incomplete';
          END IF;
          IF NEW.status IN ('failed','cancelled','timed_out') AND (
             NEW.result_state_digest IS NOT NULL OR NEW.resolved_at IS NULL) THEN
            RAISE EXCEPTION 'RunSigil terminal subworkflow call is invalid';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_immutable_subworkflow_call "
        "BEFORE UPDATE ON workflow_subworkflow_calls FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_immutable_subworkflow_call()"
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_app_workflow_cancellation_only() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF current_user = 'runsigil_app' AND NOT (
             OLD.status IN ('queued','waiting') AND NEW.status = 'cancelled'
             AND NEW.version = OLD.version + 1
             AND NEW.encrypted_state = OLD.encrypted_state
             AND NEW.state_digest = OLD.state_digest
             AND NEW.current_nodes_json::jsonb = OLD.current_nodes_json::jsonb
             AND NEW.completed_nodes_json::jsonb = OLD.completed_nodes_json::jsonb
             AND NEW.path_json::jsonb = OLD.path_json::jsonb
             AND NEW.loop_counts_json::jsonb = OLD.loop_counts_json::jsonb
             AND NEW.step_count = OLD.step_count
             AND NEW.error_code = 'workflow_cancelled'
             AND NEW.claim_token_hash IS NULL AND NEW.lease_expires_at IS NULL
             AND NEW.completed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'RunSigil app role may only cancel an idle workflow execution';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_app_workflow_cancellation_only "
        "BEFORE UPDATE ON workflow_executions FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_app_workflow_cancellation_only()"
    )

    op.execute(
        "GRANT SELECT ON workflow_subworkflow_calls TO "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute("GRANT INSERT, UPDATE ON workflow_subworkflow_calls TO runsigil_worker")
    op.execute("GRANT UPDATE ON workflow_subworkflow_calls TO runsigil_app")
    op.execute("GRANT UPDATE ON workflow_executions TO runsigil_app")
    op.execute(
        "REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON workflow_subworkflow_calls FROM "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute("REVOKE INSERT ON workflow_subworkflow_calls FROM runsigil_app")

    op.execute("ALTER TABLE workflow_subworkflow_calls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_subworkflow_calls FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY runsigil_workflow_subworkflow_calls_tenant_policy "
        "ON workflow_subworkflow_calls TO runsigil_app "
        "USING (organization_id = runsigil_request_organization()) "
        "WITH CHECK (organization_id = runsigil_request_organization())"
    )
    op.execute(
        "CREATE POLICY runsigil_workflow_subworkflow_calls_worker_policy "
        "ON workflow_subworkflow_calls TO runsigil_worker USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY runsigil_workflow_subworkflow_calls_gateway_policy "
        "ON workflow_subworkflow_calls TO runsigil_gateway_authorizer USING (true)"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0009 is intentionally irreversible because it preserves nested execution lineage"
    )
