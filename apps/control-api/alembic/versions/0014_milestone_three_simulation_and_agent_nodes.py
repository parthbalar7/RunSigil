"""Add deterministic tool simulation and durable workflow model calls.

Revision ID: 0014_m3_sim_agent
Revises: 0013_m3_tool_nodes
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014_m3_sim_agent"
down_revision = "0013_m3_tool_nodes"
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
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("organization_id", "id"),
    ]


def _enable_rls(table: str) -> None:
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


def upgrade() -> None:
    op.create_table(
        "workflow_simulation_profiles",
        *_tenant_columns(),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("tool_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=200), nullable=False),
        sa.Column("contract_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("organization_id", "project_id", "name"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "tool_id"],
            ["tools.organization_id", "tools.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("status = 'active'", name="status"),
    )
    op.create_index(
        "ix_workflow_simulation_profiles_organization_id",
        "workflow_simulation_profiles",
        ["organization_id"],
    )

    op.add_column(
        "workflow_executions",
        sa.Column(
            "execution_mode",
            sa.String(length=30),
            nullable=False,
            server_default="live",
        ),
    )
    op.add_column(
        "workflow_executions",
        sa.Column("simulation_profile_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workflow_execution_simulation_profile",
        "workflow_executions",
        "workflow_simulation_profiles",
        ["organization_id", "simulation_profile_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_workflow_execution_mode",
        "workflow_executions",
        "(execution_mode = 'live' AND simulation_profile_id IS NULL) OR "
        "(execution_mode = 'simulation' AND simulation_profile_id IS NOT NULL)",
    )

    op.add_column(
        "evaluations",
        sa.Column("simulation_profile_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_evaluation_simulation_profile",
        "evaluations",
        "workflow_simulation_profiles",
        ["organization_id", "simulation_profile_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "workflow_tool_simulation_calls",
        *_tenant_columns(),
        sa.Column("workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("simulation_profile_id", sa.Uuid(), nullable=False),
        sa.Column("tool_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("result_state_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("arguments_digest", sa.String(length=71), nullable=False),
        sa.Column("tool_digest", sa.String(length=71), nullable=False),
        sa.Column("profile_digest", sa.String(length=71), nullable=False),
        sa.Column("result_digest", sa.String(length=71), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
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
            name="fk_workflow_tool_simulation_execution_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "simulation_profile_id"],
            ["workflow_simulation_profiles.organization_id", "workflow_simulation_profiles.id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_simulation_profile",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "tool_id"],
            ["tools.organization_id", "tools.id"],
            ondelete="RESTRICT",
            name="fk_workflow_tool_simulation_tool",
        ),
        sa.CheckConstraint("status = 'completed'", name="status"),
    )
    op.create_index(
        "ix_workflow_tool_simulation_calls_organization_id",
        "workflow_tool_simulation_calls",
        ["organization_id"],
    )

    op.create_table(
        "model_calls",
        *_tenant_columns(),
        sa.Column("workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("model_route_id", sa.Uuid(), nullable=False),
        sa.Column("delegation_id", sa.Uuid(), nullable=False),
        sa.Column("policy_decision_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("input_state_key", sa.String(length=100), nullable=False),
        sa.Column("result_state_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("request_digest", sa.String(length=71), nullable=False),
        sa.Column("route_digest", sa.String(length=71), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("encrypted_request", sa.Text(), nullable=False),
        sa.Column("output_digest", sa.String(length=71), nullable=True),
        sa.Column("encrypted_output", sa.Text(), nullable=True),
        sa.Column("provider_reference", sa.String(length=300), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cost_minor", sa.BigInteger(), nullable=True),
        sa.Column("worker_name", sa.String(length=200), nullable=True),
        sa.Column("claim_token_hash", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execute_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reconcile_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "workflow_execution_id", "node_id", "sequence"
        ),
        sa.UniqueConstraint("organization_id", "idempotency_key"),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id", "run_id"],
            [
                "workflow_executions.organization_id",
                "workflow_executions.id",
                "workflow_executions.run_id",
            ],
            ondelete="RESTRICT",
            name="fk_model_call_execution_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "model_route_id"],
            ["model_routes.organization_id", "model_routes.id"],
            ondelete="RESTRICT",
            name="fk_model_call_route",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "delegation_id"],
            ["delegations.organization_id", "delegations.id"],
            ondelete="RESTRICT",
            name="fk_model_call_delegation",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_decision_id"],
            ["workflow_policy_decisions.organization_id", "workflow_policy_decisions.id"],
            ondelete="RESTRICT",
            name="fk_model_call_policy_decision",
        ),
        sa.CheckConstraint(
            "status IN ('queued','executing','reconciliation_required','reconciling',"
            "'completed','failed','timed_out')",
            name="status",
        ),
        sa.CheckConstraint(
            "max_output_tokens > 0 AND max_output_tokens <= 32768",
            name="max_output_tokens",
        ),
    )
    op.create_index(
        "ix_model_calls_organization_id", "model_calls", ["organization_id"]
    )
    op.create_index(
        "ix_model_calls_ready",
        "model_calls",
        ["status", "next_reconcile_at", "lease_expires_at"],
    )

    op.create_table(
        "model_call_budget_reservations",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("model_call_id", sa.Uuid(), nullable=False),
        sa.Column("budget_reservation_id", sa.Uuid(), nullable=False),
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
        sa.PrimaryKeyConstraint("model_call_id", "budget_reservation_id"),
        sa.UniqueConstraint(
            "organization_id", "model_call_id", "budget_reservation_id"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "model_call_id"],
            ["model_calls.organization_id", "model_calls.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "budget_reservation_id"],
            ["budget_reservations.organization_id", "budget_reservations.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_model_call_budget_reservations_organization_id",
        "model_call_budget_reservations",
        ["organization_id"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION runsigil_immutable_workflow_execution_lineage()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id OR NEW.run_id <> OLD.run_id
             OR NEW.workflow_version_id <> OLD.workflow_version_id
             OR NEW.deployment_id <> OLD.deployment_id
             OR NEW.evaluation_id IS DISTINCT FROM OLD.evaluation_id
             OR NEW.evaluation_scenario_id IS DISTINCT FROM OLD.evaluation_scenario_id
             OR NEW.forked_from_checkpoint_id IS DISTINCT FROM OLD.forked_from_checkpoint_id
             OR NEW.execution_mode <> OLD.execution_mode
             OR NEW.simulation_profile_id IS DISTINCT FROM OLD.simulation_profile_id
             OR NEW.content_digest <> OLD.content_digest
             OR NEW.max_steps <> OLD.max_steps OR NEW.deadline_at <> OLD.deadline_at THEN
            RAISE EXCEPTION 'RunSigil workflow execution lineage is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_evaluation_lineage() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id
             OR NEW.workflow_version_id <> OLD.workflow_version_id
             OR NEW.dataset_version_id <> OLD.dataset_version_id
             OR NEW.deployment_id <> OLD.deployment_id
             OR NEW.baseline_evaluation_id IS DISTINCT FROM OLD.baseline_evaluation_id
             OR NEW.simulation_profile_id IS DISTINCT FROM OLD.simulation_profile_id
             OR NEW.actor_id <> OLD.actor_id
             OR NEW.idempotency_key <> OLD.idempotency_key
             OR NEW.minimum_score_milli <> OLD.minimum_score_milli
             OR NEW.maximum_regression_milli <> OLD.maximum_regression_milli
             OR NEW.content_digest <> OLD.content_digest THEN
            RAISE EXCEPTION 'RunSigil evaluation lineage is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_immutable_evaluation_lineage "
        "BEFORE UPDATE ON evaluations FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_immutable_evaluation_lineage()"
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_simulation_profiles_append_only "
        "BEFORE UPDATE OR DELETE ON workflow_simulation_profiles FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_append_only()"
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_tool_simulation_calls_append_only "
        "BEFORE UPDATE OR DELETE ON workflow_tool_simulation_calls FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_append_only()"
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_model_call_budgets_append_only "
        "BEFORE UPDATE OR DELETE ON model_call_budget_reservations FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_append_only()"
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_model_call() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id
             OR NEW.workflow_execution_id <> OLD.workflow_execution_id
             OR NEW.run_id <> OLD.run_id
             OR NEW.model_route_id <> OLD.model_route_id
             OR NEW.delegation_id <> OLD.delegation_id
             OR NEW.policy_decision_id <> OLD.policy_decision_id
             OR NEW.node_id <> OLD.node_id OR NEW.sequence <> OLD.sequence
             OR NEW.input_state_key <> OLD.input_state_key
             OR NEW.result_state_key <> OLD.result_state_key
             OR NEW.request_digest <> OLD.request_digest
             OR NEW.route_digest <> OLD.route_digest
             OR NEW.content_digest <> OLD.content_digest
             OR NEW.encrypted_request <> OLD.encrypted_request
             OR NEW.idempotency_key <> OLD.idempotency_key
             OR NEW.max_output_tokens <> OLD.max_output_tokens
             OR NEW.expires_at <> OLD.expires_at THEN
            RAISE EXCEPTION 'RunSigil model call authorization lineage is immutable';
          END IF;
          IF OLD.status IN ('completed','failed','timed_out') AND (
             NEW.status <> OLD.status
             OR NEW.output_digest IS DISTINCT FROM OLD.output_digest
             OR NEW.encrypted_output IS DISTINCT FROM OLD.encrypted_output
             OR NEW.provider_reference IS DISTINCT FROM OLD.provider_reference
             OR NEW.input_tokens IS DISTINCT FROM OLD.input_tokens
             OR NEW.output_tokens IS DISTINCT FROM OLD.output_tokens
             OR NEW.cost_minor IS DISTINCT FROM OLD.cost_minor
             OR NEW.completed_at IS DISTINCT FROM OLD.completed_at) THEN
            RAISE EXCEPTION 'RunSigil model call settlement is single use';
          END IF;
          IF NEW.status = 'completed' AND (
             NEW.output_digest IS NULL OR NEW.encrypted_output IS NULL
             OR NEW.input_tokens IS NULL OR NEW.output_tokens IS NULL
             OR NEW.cost_minor IS NULL OR NEW.completed_at IS NULL) THEN
            RAISE EXCEPTION 'RunSigil completed model call is incomplete';
          END IF;
          IF NEW.status IN ('failed','timed_out') AND (
             NEW.output_digest IS NOT NULL OR NEW.encrypted_output IS NOT NULL
             OR NEW.completed_at IS NULL) THEN
            RAISE EXCEPTION 'RunSigil terminal model call is invalid';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_immutable_model_call "
        "BEFORE UPDATE ON model_calls FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_immutable_model_call()"
    )

    op.execute(
        "GRANT SELECT ON workflow_simulation_profiles, workflow_tool_simulation_calls, "
        "model_calls, model_call_budget_reservations TO "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute("GRANT INSERT ON workflow_simulation_profiles TO runsigil_app")
    op.execute(
        "GRANT INSERT ON workflow_tool_simulation_calls TO runsigil_worker"
    )
    op.execute("GRANT INSERT, UPDATE ON model_calls TO runsigil_worker")
    op.execute(
        "GRANT INSERT ON model_call_budget_reservations TO runsigil_worker"
    )
    op.execute(
        "REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON "
        "workflow_simulation_profiles, workflow_tool_simulation_calls, model_calls, "
        "model_call_budget_reservations FROM "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE INSERT, UPDATE ON workflow_simulation_profiles FROM "
        "runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE INSERT, UPDATE ON workflow_tool_simulation_calls FROM "
        "runsigil_app, runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE INSERT, UPDATE ON model_calls, model_call_budget_reservations FROM "
        "runsigil_app, runsigil_gateway_authorizer"
    )
    op.execute("REVOKE UPDATE ON model_call_budget_reservations FROM runsigil_worker")

    for table in (
        "workflow_simulation_profiles",
        "workflow_tool_simulation_calls",
        "model_calls",
        "model_call_budget_reservations",
    ):
        _enable_rls(table)


def downgrade() -> None:
    raise RuntimeError(
        "0014 is intentionally irreversible because it preserves simulation and model lineage"
    )
