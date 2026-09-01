"""Add durable workflow waits and append-only evaluation annotations.

Revision ID: 0007_m3_waits
Revises: 0006_m3_lineage
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_m3_waits"
down_revision = "0006_m3_lineage"
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
        "workflow_waits",
        *_tenant_columns(),
        sa.Column("workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("wait_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("resolution", sa.String(length=30), nullable=True),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("state_digest", sa.String(length=71), nullable=False),
        sa.Column("request_metadata_json", sa.JSON(), nullable=False),
        sa.Column("event_key", sa.String(length=100), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_digest", sa.String(length=71), nullable=True),
        sa.Column("encrypted_response", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "workflow_execution_id", "node_id", "sequence"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_wait_execution_run",
        ),
        sa.CheckConstraint(
            "wait_type IN ('timer','event','approval','request_information')",
            name="wait_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','resolved','expired')",
            name="status",
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN "
            "('elapsed','approved','denied','received','expired')",
            name="resolution",
        ),
    )
    op.create_index("ix_workflow_waits_organization_id", "workflow_waits", ["organization_id"])
    op.create_index(
        "ix_workflow_waits_pending",
        "workflow_waits",
        ["organization_id", "status", "expires_at"],
    )

    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_evaluation_result_annotation_lineage'
          ) THEN
            ALTER TABLE evaluation_results
              ADD CONSTRAINT uq_evaluation_result_annotation_lineage
              UNIQUE (organization_id, id, evaluation_id, scenario_id, run_id);
          END IF;
        END $$
        """
    )
    op.create_table(
        "evaluation_annotations",
        *_tenant_columns(),
        sa.Column("evaluation_result_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=30), nullable=False),
        sa.Column("score_milli", sa.Integer(), nullable=True),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.UniqueConstraint("organization_id", "idempotency_key"),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "evaluation_result_id",
                "evaluation_id",
                "scenario_id",
                "run_id",
            ],
            [
                "evaluation_results.organization_id",
                "evaluation_results.id",
                "evaluation_results.evaluation_id",
                "evaluation_results.scenario_id",
                "evaluation_results.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_evaluation_annotation_result_lineage",
        ),
        sa.CheckConstraint(
            "label IN ('passed','failed','needs_review')",
            name="label",
        ),
        sa.CheckConstraint(
            "score_milli IS NULL OR (score_milli >= 0 AND score_milli <= 1000)",
            name="score",
        ),
    )
    op.create_index(
        "ix_evaluation_annotations_organization_id",
        "evaluation_annotations",
        ["organization_id"],
    )

    op.execute("ALTER TABLE runs DROP CONSTRAINT ck_runs_status")
    op.execute(
        "ALTER TABLE runs ADD CONSTRAINT ck_runs_status CHECK "
        "(status IN ('authorizing','waiting_for_approval','waiting','queued','running',"
        "'completed','failed','cancelled','reconciliation_required','dead_lettered'))"
    )
    op.execute(
        "ALTER TABLE workflow_executions DROP CONSTRAINT ck_workflow_executions_status"
    )
    op.execute(
        "ALTER TABLE workflow_executions ADD CONSTRAINT ck_workflow_executions_status "
        "CHECK (status IN ('queued','running','waiting','completed','failed','cancelled'))"
    )

    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_workflow_wait_lineage() RETURNS trigger
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
             NEW.status <> OLD.status
             OR NEW.resolution IS DISTINCT FROM OLD.resolution
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
        "CREATE TRIGGER trg_runsigil_immutable_workflow_wait_lineage "
        "BEFORE UPDATE ON workflow_waits FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_immutable_workflow_wait_lineage()"
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_evaluation_annotations_append_only "
        "BEFORE UPDATE OR DELETE ON evaluation_annotations FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_append_only()"
    )

    op.execute(
        "GRANT SELECT ON workflow_waits, evaluation_annotations TO "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute("GRANT UPDATE ON workflow_waits TO runsigil_app")
    op.execute("GRANT INSERT, UPDATE ON workflow_waits TO runsigil_worker")
    op.execute("GRANT INSERT ON evaluation_annotations TO runsigil_app")
    op.execute(
        "REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON "
        "workflow_waits, evaluation_annotations FROM "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE INSERT ON workflow_waits FROM runsigil_app, runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE UPDATE ON evaluation_annotations FROM "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )

    for table in ("workflow_waits", "evaluation_annotations"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY runsigil_{table}_tenant_policy ON {table} TO runsigil_app "
            "USING (organization_id = runsigil_request_organization()) "
            "WITH CHECK (organization_id = runsigil_request_organization())"
        )
        op.execute(
            f"CREATE POLICY runsigil_{table}_worker_policy ON {table} TO runsigil_worker "
            "USING (true) WITH CHECK (true)"
        )
        op.execute(
            f"CREATE POLICY runsigil_{table}_gateway_policy ON {table} "
            "TO runsigil_gateway_authorizer USING (true)"
        )


def downgrade() -> None:
    raise RuntimeError("0007 is intentionally irreversible because it adds durable wait lineage")
