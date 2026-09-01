"""Add governed workflow tool nodes and exact effect lineage.

Revision ID: 0013_m3_tool_nodes
Revises: 0012_m3_cancel_acl
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013_m3_tool_nodes"
down_revision = "0012_m3_cancel_acl"
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
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'runs'
              AND column_name = 'actor_type'
          ) THEN
            ALTER TABLE runs ADD COLUMN actor_type varchar(30)
              NOT NULL DEFAULT 'user';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_runs_actor_type'
          ) THEN
            ALTER TABLE runs ADD CONSTRAINT ck_runs_actor_type
              CHECK (actor_type IN ('user','service','workload'));
          END IF;
          ALTER TABLE runs ALTER COLUMN actor_type SET DEFAULT 'user';
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_intent_identity_run'
          ) THEN
            ALTER TABLE intents ADD CONSTRAINT uq_intent_identity_run
              UNIQUE (organization_id, id, run_id);
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_action_identity_run'
          ) THEN
            ALTER TABLE actions ADD CONSTRAINT uq_action_identity_run
              UNIQUE (organization_id, id, run_id);
          END IF;
        END $$
        """
    )

    op.create_table(
        "workflow_tool_calls",
        *_tenant_columns(),
        sa.Column("workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=False),
        sa.Column("child_run_id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("tool_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("result_state_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("arguments_digest", sa.String(length=71), nullable=False),
        sa.Column("tool_digest", sa.String(length=71), nullable=False),
        sa.Column("action_content_digest", sa.String(length=71), nullable=False),
        sa.Column("result_digest", sa.String(length=71), nullable=True),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "workflow_execution_id", "node_id", "sequence"),
        sa.UniqueConstraint("organization_id", "child_run_id"),
        sa.UniqueConstraint("organization_id", "action_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "parent_run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_tool_call_parent_execution_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "child_run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_call_child_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "action_id", "child_run_id"],
            ["actions.organization_id", "actions.id", "actions.run_id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_call_action_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "intent_id", "child_run_id"],
            ["intents.organization_id", "intents.id", "intents.run_id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_call_intent_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "tool_id"],
            ["tools.organization_id", "tools.id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_call_tool",
        ),
        sa.CheckConstraint(
            "status IN ('pending_approval','queued','executing','reconciliation_required',"
            "'reconciling','completed','failed','dead_lettered','cancelled','timed_out')",
            name="status",
        ),
    )
    op.create_index(
        "ix_workflow_tool_calls_organization_id",
        "workflow_tool_calls",
        ["organization_id"],
    )
    op.create_index(
        "ix_workflow_tool_calls_parent",
        "workflow_tool_calls",
        ["organization_id", "workflow_execution_id", "sequence", "status"],
    )

    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_workflow_tool_call() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id
             OR NEW.workflow_execution_id <> OLD.workflow_execution_id
             OR NEW.parent_run_id <> OLD.parent_run_id
             OR NEW.child_run_id <> OLD.child_run_id
             OR NEW.action_id <> OLD.action_id OR NEW.intent_id <> OLD.intent_id
             OR NEW.tool_id <> OLD.tool_id OR NEW.node_id <> OLD.node_id
             OR NEW.sequence <> OLD.sequence
             OR NEW.result_state_key <> OLD.result_state_key
             OR NEW.arguments_digest <> OLD.arguments_digest
             OR NEW.tool_digest <> OLD.tool_digest
             OR NEW.action_content_digest <> OLD.action_content_digest
             OR NEW.content_digest <> OLD.content_digest
             OR NEW.expires_at <> OLD.expires_at THEN
            RAISE EXCEPTION 'RunSigil workflow tool call lineage is immutable';
          END IF;
          IF OLD.status IN ('completed','failed','cancelled','timed_out')
             AND (NEW.status <> OLD.status
                  OR NEW.result_digest IS DISTINCT FROM OLD.result_digest
                  OR NEW.resolved_at IS DISTINCT FROM OLD.resolved_at) THEN
            RAISE EXCEPTION 'RunSigil workflow tool call settlement is single use';
          END IF;
          IF NEW.status = 'completed' AND (
             NEW.result_digest IS NULL OR NEW.resolved_at IS NULL) THEN
            RAISE EXCEPTION 'RunSigil completed workflow tool call is incomplete';
          END IF;
          IF NEW.status IN ('failed','cancelled','timed_out') AND (
             NEW.result_digest IS NOT NULL OR NEW.resolved_at IS NULL) THEN
            RAISE EXCEPTION 'RunSigil terminal workflow tool call is invalid';
          END IF;
          IF NEW.status NOT IN ('completed','failed','cancelled','timed_out')
             AND (NEW.result_digest IS NOT NULL OR NEW.resolved_at IS NOT NULL) THEN
            RAISE EXCEPTION 'RunSigil pending workflow tool call cannot contain a result';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_immutable_workflow_tool_call "
        "BEFORE UPDATE ON workflow_tool_calls FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_immutable_workflow_tool_call()"
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_app_workflow_tool_call_transition() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF current_user = 'runsigil_app' AND NOT (
             (OLD.status = 'pending_approval'
              AND NEW.status IN ('queued','failed','cancelled')
              AND NEW.result_digest IS NULL
              AND ((NEW.status = 'queued' AND NEW.resolved_at IS NULL)
                   OR (NEW.status IN ('failed','cancelled')
                       AND NEW.resolved_at IS NOT NULL)))
             OR (OLD.status = 'dead_lettered'
                 AND NEW.status = 'reconciliation_required'
                 AND NEW.result_digest IS NULL AND NEW.resolved_at IS NULL)) THEN
            RAISE EXCEPTION 'RunSigil app role may only decide or cancel a pending tool call';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_app_workflow_tool_call_transition "
        "BEFORE UPDATE ON workflow_tool_calls FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_app_workflow_tool_call_transition()"
    )

    op.execute(
        "GRANT SELECT ON workflow_tool_calls TO "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute("GRANT INSERT, UPDATE ON workflow_tool_calls TO runsigil_worker")
    op.execute("GRANT UPDATE ON workflow_tool_calls TO runsigil_app")
    op.execute("GRANT INSERT ON action_budget_reservations TO runsigil_worker")
    op.execute(
        "REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON workflow_tool_calls FROM "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute("REVOKE INSERT ON workflow_tool_calls FROM runsigil_app")

    op.execute("ALTER TABLE workflow_tool_calls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_tool_calls FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY runsigil_workflow_tool_calls_tenant_policy "
        "ON workflow_tool_calls TO runsigil_app "
        "USING (organization_id = runsigil_request_organization()) "
        "WITH CHECK (organization_id = runsigil_request_organization())"
    )
    op.execute(
        "CREATE POLICY runsigil_workflow_tool_calls_worker_policy "
        "ON workflow_tool_calls TO runsigil_worker USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY runsigil_workflow_tool_calls_gateway_policy "
        "ON workflow_tool_calls TO runsigil_gateway_authorizer USING (true)"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0013 is intentionally irreversible because it preserves governed tool-effect lineage"
    )
