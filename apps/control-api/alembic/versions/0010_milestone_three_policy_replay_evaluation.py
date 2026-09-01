"""Add per-node policy evidence, deterministic replay, and safety graders.

Revision ID: 0010_m3_policy_replay
Revises: 0009_m3_subflows
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010_m3_policy_replay"
down_revision = "0009_m3_subflows"
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
        "workflow_policy_decisions",
        *_tenant_columns(),
        sa.Column("workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("evaluation", sa.Integer(), nullable=False),
        sa.Column("policy_bundle_id", sa.Uuid(), nullable=False),
        sa.Column("effect", sa.String(length=40), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("input_digest", sa.String(length=71), nullable=False),
        sa.Column("policy_digest", sa.String(length=71), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "workflow_execution_id",
            "node_id",
            "sequence",
            "evaluation",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_policy_decision_execution_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_bundle_id"],
            ["policy_bundles.organization_id", "policy_bundles.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("evaluation > 0", name="evaluation_positive"),
        sa.CheckConstraint(
            "effect IN ('allow','deny','require_approval','require_information',"
            "'transform','redact','rate_limit','quarantine')",
            name="effect",
        ),
    )
    op.create_index(
        "ix_workflow_policy_decisions_organization_id",
        "workflow_policy_decisions",
        ["organization_id"],
    )
    op.create_index(
        "ix_workflow_policy_decisions_execution",
        "workflow_policy_decisions",
        ["organization_id", "workflow_execution_id", "node_id", "sequence"],
    )

    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_run_checkpoint_replay_lineage'
          ) THEN
            ALTER TABLE run_checkpoints ADD CONSTRAINT uq_run_checkpoint_replay_lineage
              UNIQUE (organization_id, id, workflow_execution_id, run_id);
          END IF;
        END $$
        """
    )
    op.create_table(
        "workflow_replays",
        *_tenant_columns(),
        sa.Column("source_workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("replay_workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("replay_run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("source_state_digest", sa.String(length=71), nullable=False),
        sa.Column("source_path_digest", sa.String(length=71), nullable=False),
        sa.Column("replay_state_digest", sa.String(length=71), nullable=True),
        sa.Column("replay_path_digest", sa.String(length=71), nullable=True),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "replay_run_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_workflow_execution_id", "source_run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_replay_source_execution_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "replay_workflow_execution_id", "replay_run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_replay_execution_run",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "source_checkpoint_id",
                "source_workflow_execution_id",
                "source_run_id",
            ],
            [
                "run_checkpoints.organization_id",
                "run_checkpoints.id",
                "run_checkpoints.workflow_execution_id",
                "run_checkpoints.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_replay_source_checkpoint",
        ),
        sa.CheckConstraint(
            "status IN ('running','matched','diverged','failed','cancelled')",
            name="status",
        ),
    )
    op.create_index(
        "ix_workflow_replays_organization_id",
        "workflow_replays",
        ["organization_id"],
    )

    op.add_column(
        "evaluation_results",
        sa.Column(
            "policy_outcome",
            sa.String(length=30),
            nullable=False,
            server_default="passed",
        ),
    )
    op.add_column(
        "evaluation_results",
        sa.Column(
            "safety_outcome",
            sa.String(length=30),
            nullable=False,
            server_default="passed",
        ),
    )
    op.alter_column("evaluation_results", "policy_outcome", server_default=None)
    op.alter_column("evaluation_results", "safety_outcome", server_default=None)
    op.create_check_constraint(
        "ck_evaluation_results_policy_outcome",
        "evaluation_results",
        "policy_outcome IN ('passed','failed')",
    )
    op.create_check_constraint(
        "ck_evaluation_results_safety_outcome",
        "evaluation_results",
        "safety_outcome IN ('passed','failed')",
    )

    op.execute(
        "CREATE TRIGGER trg_runsigil_workflow_policy_decisions_append_only "
        "BEFORE UPDATE OR DELETE ON workflow_policy_decisions FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_append_only()"
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_workflow_replay() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id
             OR NEW.source_workflow_execution_id <> OLD.source_workflow_execution_id
             OR NEW.source_run_id <> OLD.source_run_id
             OR NEW.source_checkpoint_id <> OLD.source_checkpoint_id
             OR NEW.replay_workflow_execution_id <> OLD.replay_workflow_execution_id
             OR NEW.replay_run_id <> OLD.replay_run_id
             OR NEW.source_state_digest <> OLD.source_state_digest
             OR NEW.source_path_digest <> OLD.source_path_digest
             OR NEW.content_digest <> OLD.content_digest THEN
            RAISE EXCEPTION 'RunSigil workflow replay lineage is immutable';
          END IF;
          IF OLD.status <> 'running' AND (
             NEW.status <> OLD.status
             OR NEW.replay_state_digest IS DISTINCT FROM OLD.replay_state_digest
             OR NEW.replay_path_digest IS DISTINCT FROM OLD.replay_path_digest
             OR NEW.completed_at IS DISTINCT FROM OLD.completed_at) THEN
            RAISE EXCEPTION 'RunSigil workflow replay settlement is single use';
          END IF;
          IF NEW.status = 'running' AND (
             NEW.replay_state_digest IS NOT NULL OR NEW.replay_path_digest IS NOT NULL
             OR NEW.completed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'RunSigil running workflow replay cannot contain a result';
          END IF;
          IF NEW.status IN ('matched','diverged') AND (
             NEW.replay_state_digest IS NULL OR NEW.replay_path_digest IS NULL
             OR NEW.completed_at IS NULL) THEN
            RAISE EXCEPTION 'RunSigil completed workflow replay is incomplete';
          END IF;
          IF NEW.status IN ('failed','cancelled') AND NEW.completed_at IS NULL THEN
            RAISE EXCEPTION 'RunSigil terminal workflow replay is incomplete';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_immutable_workflow_replay "
        "BEFORE UPDATE ON workflow_replays FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_immutable_workflow_replay()"
    )

    table_list = "workflow_policy_decisions, workflow_replays"
    op.execute(
        f"GRANT SELECT ON {table_list} TO "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute("GRANT INSERT ON workflow_replays TO runsigil_app")
    op.execute("GRANT INSERT ON workflow_policy_decisions TO runsigil_worker")
    op.execute("GRANT UPDATE ON workflow_replays TO runsigil_worker")
    op.execute(
        f"REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON {table_list} FROM "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute("REVOKE UPDATE ON workflow_policy_decisions FROM runsigil_worker")
    op.execute("REVOKE INSERT, UPDATE ON workflow_policy_decisions FROM runsigil_app")
    op.execute("REVOKE INSERT, UPDATE ON workflow_replays FROM runsigil_gateway_authorizer")
    op.execute("REVOKE INSERT ON workflow_replays FROM runsigil_worker")
    op.execute("REVOKE UPDATE ON workflow_replays FROM runsigil_app")

    for table in ("workflow_policy_decisions", "workflow_replays"):
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
    raise RuntimeError(
        "0010 is intentionally irreversible because it preserves policy and replay evidence"
    )
