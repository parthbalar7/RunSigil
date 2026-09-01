"""Milestone 3 workflow engine and deterministic evaluation foundation.

Revision ID: 0004_m3_workflow
Revises: 0003_m2_lineage
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0004_m3_workflow"
down_revision = "0003_m2_lineage"
branch_labels = None
depends_on = None

NEW_TENANT_TABLES = [
    "workflows",
    "workflow_versions",
    "workflow_deployments",
    "evaluation_datasets",
    "evaluation_dataset_versions",
    "evaluation_scenarios",
    "evaluations",
    "workflow_executions",
    "workflow_node_attempts",
    "run_checkpoints",
    "evaluation_results",
]


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


def _create_workflow_tables() -> None:
    op.create_table(
        "workflows",
        *_tenant_columns(),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.UniqueConstraint("organization_id", "project_id", "slug"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "workflow_versions",
        *_tenant_columns(),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("definition_digest", sa.String(length=71), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("organization_id", "workflow_id", "version"),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_id"],
            ["workflows.organization_id", "workflows.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "workflow_deployments",
        *_tenant_columns(),
        sa.Column("workflow_version_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("deployed_by", sa.Uuid(), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "workflow_version_id", "environment_id", "agent_id"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_version_id"],
            ["workflow_versions.organization_id", "workflow_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "environment_id"],
            ["environments.organization_id", "environments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "agent_id"],
            ["agents.organization_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "evaluation_datasets",
        *_tenant_columns(),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.UniqueConstraint("organization_id", "project_id", "slug"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "evaluation_dataset_versions",
        *_tenant_columns(),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("scenario_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("organization_id", "dataset_id", "version"),
        sa.ForeignKeyConstraint(
            ["organization_id", "dataset_id"],
            ["evaluation_datasets.organization_id", "evaluation_datasets.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "evaluation_scenarios",
        *_tenant_columns(),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("input_digest", sa.String(length=71), nullable=False),
        sa.Column("expected_output_digest", sa.String(length=71), nullable=False),
        sa.Column("expected_path_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.UniqueConstraint("organization_id", "dataset_version_id", "scenario_key"),
        sa.ForeignKeyConstraint(
            ["organization_id", "dataset_version_id"],
            ["evaluation_dataset_versions.organization_id", "evaluation_dataset_versions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "evaluations",
        *_tenant_columns(),
        sa.Column("workflow_version_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_evaluation_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("minimum_score_milli", sa.Integer(), nullable=False),
        sa.Column("maximum_regression_milli", sa.Integer(), nullable=False),
        sa.Column("score_milli", sa.Integer(), nullable=True),
        sa.Column("baseline_score_milli", sa.Integer(), nullable=True),
        sa.Column("score_delta_milli", sa.Integer(), nullable=True),
        sa.Column("regression_status", sa.String(length=30), nullable=True),
        sa.Column("release_gate_status", sa.String(length=30), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "idempotency_key"),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_version_id"],
            ["workflow_versions.organization_id", "workflow_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "dataset_version_id"],
            ["evaluation_dataset_versions.organization_id", "evaluation_dataset_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "deployment_id"],
            ["workflow_deployments.organization_id", "workflow_deployments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "baseline_evaluation_id"],
            ["evaluations.organization_id", "evaluations.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "workflow_executions",
        *_tenant_columns(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version_id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(), nullable=True),
        sa.Column("evaluation_scenario_id", sa.Uuid(), nullable=True),
        sa.Column("forked_from_checkpoint_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("encrypted_state", sa.Text(), nullable=False),
        sa.Column("state_digest", sa.String(length=71), nullable=False),
        sa.Column("current_nodes_json", sa.JSON(), nullable=False),
        sa.Column("completed_nodes_json", sa.JSON(), nullable=False),
        sa.Column("path_json", sa.JSON(), nullable=False),
        sa.Column("loop_counts_json", sa.JSON(), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("worker_name", sa.String(length=200), nullable=True),
        sa.Column("claim_token_hash", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.UniqueConstraint("organization_id", "run_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_version_id"],
            ["workflow_versions.organization_id", "workflow_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "deployment_id"],
            ["workflow_deployments.organization_id", "workflow_deployments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "evaluation_id"],
            ["evaluations.organization_id", "evaluations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "evaluation_scenario_id"],
            ["evaluation_scenarios.organization_id", "evaluation_scenarios.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "workflow_node_attempts",
        *_tenant_columns(),
        sa.Column("workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("node_type", sa.String(length=40), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("input_digest", sa.String(length=71), nullable=False),
        sa.Column("output_digest", sa.String(length=71), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.UniqueConstraint("organization_id", "workflow_execution_id", "node_id", "attempt"),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id"],
            ["workflow_executions.organization_id", "workflow_executions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "run_checkpoints",
        *_tenant_columns(),
        sa.Column("workflow_execution_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("encrypted_state", sa.Text(), nullable=False),
        sa.Column("state_digest", sa.String(length=71), nullable=False),
        sa.Column("active_nodes_json", sa.JSON(), nullable=False),
        sa.Column("completed_nodes_json", sa.JSON(), nullable=False),
        sa.Column("path_json", sa.JSON(), nullable=False),
        sa.Column("loop_counts_json", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Uuid(), nullable=True),
        sa.UniqueConstraint("organization_id", "run_id", "sequence"),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_execution_id"],
            ["workflow_executions.organization_id", "workflow_executions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "parent_checkpoint_id"],
            ["run_checkpoints.organization_id", "run_checkpoints.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_foreign_key(
        "fk_workflow_executions_organization_id_run_checkpoints",
        "workflow_executions",
        "run_checkpoints",
        ["organization_id", "forked_from_checkpoint_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "evaluation_results",
        *_tenant_columns(),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("score_milli", sa.Integer(), nullable=False),
        sa.Column("task_outcome", sa.String(length=30), nullable=False),
        sa.Column("trajectory_outcome", sa.String(length=30), nullable=False),
        sa.Column("deterministic_environment_outcome", sa.String(length=30), nullable=False),
        sa.Column("output_digest", sa.String(length=71), nullable=False),
        sa.Column("trajectory_digest", sa.String(length=71), nullable=False),
        sa.Column("graders_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("organization_id", "evaluation_id", "scenario_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "evaluation_id"],
            ["evaluations.organization_id", "evaluations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "scenario_id"],
            ["evaluation_scenarios.organization_id", "evaluation_scenarios.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            ondelete="RESTRICT",
        ),
    )
    for table in NEW_TENANT_TABLES:
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])


def upgrade() -> None:
    bind = op.get_bind()
    run_columns = {column["name"] for column in inspect(bind).get_columns("runs")}
    if "run_kind" not in run_columns:
        op.execute(
            "ALTER TABLE runs ADD COLUMN run_kind varchar(30) "
            "NOT NULL DEFAULT 'governed_action'"
        )

    _create_workflow_tables()

    op.execute(
        "ALTER TABLE runs ADD CONSTRAINT ck_runs_run_kind "
        "CHECK (run_kind IN ('governed_action','workflow'))"
    )
    op.execute(
        "ALTER TABLE workflow_versions ADD CONSTRAINT ck_workflow_versions_status "
        "CHECK (status IN ('invalid','validated','deployed','retired'))"
    )
    op.execute(
        "ALTER TABLE workflow_deployments ADD CONSTRAINT ck_workflow_deployments_status "
        "CHECK (status IN ('active','superseded','paused'))"
    )
    op.execute(
        "ALTER TABLE workflow_executions ADD CONSTRAINT ck_workflow_executions_status "
        "CHECK (status IN ('queued','running','completed','failed','cancelled'))"
    )
    op.execute(
        "ALTER TABLE workflow_node_attempts ADD CONSTRAINT ck_workflow_node_attempts_status "
        "CHECK (status IN ('running','completed','failed'))"
    )
    op.execute(
        "ALTER TABLE evaluation_dataset_versions ADD CONSTRAINT "
        "ck_evaluation_dataset_versions_status CHECK (status IN ('active','retired'))"
    )
    op.execute(
        "ALTER TABLE evaluations ADD CONSTRAINT ck_evaluations_status "
        "CHECK (status IN ('running','completed','failed'))"
    )
    op.execute(
        "ALTER TABLE evaluations ADD CONSTRAINT ck_evaluations_release_gate "
        "CHECK (release_gate_status IN ('pending','passed','failed'))"
    )
    op.execute(
        "ALTER TABLE evaluation_results ADD CONSTRAINT ck_evaluation_results_status "
        "CHECK (status IN ('passed','failed'))"
    )

    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_workflow_version() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id
             OR NEW.workflow_id <> OLD.workflow_id
             OR NEW.version <> OLD.version
             OR NEW.definition_json::jsonb IS DISTINCT FROM OLD.definition_json::jsonb
             OR NEW.definition_digest <> OLD.definition_digest
             OR NEW.validation_json::jsonb IS DISTINCT FROM OLD.validation_json::jsonb
             OR NEW.created_by <> OLD.created_by THEN
            RAISE EXCEPTION 'RunSigil workflow version content is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_immutable_workflow_version "
        "BEFORE UPDATE ON workflow_versions FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_immutable_workflow_version()"
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_workflow_execution_lineage() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id OR NEW.run_id <> OLD.run_id
             OR NEW.workflow_version_id <> OLD.workflow_version_id
             OR NEW.deployment_id <> OLD.deployment_id
             OR NEW.evaluation_id IS DISTINCT FROM OLD.evaluation_id
             OR NEW.evaluation_scenario_id IS DISTINCT FROM OLD.evaluation_scenario_id
             OR NEW.forked_from_checkpoint_id IS DISTINCT FROM OLD.forked_from_checkpoint_id
             OR NEW.content_digest <> OLD.content_digest
             OR NEW.max_steps <> OLD.max_steps OR NEW.deadline_at <> OLD.deadline_at THEN
            RAISE EXCEPTION 'RunSigil workflow execution lineage is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_immutable_workflow_execution_lineage "
        "BEFORE UPDATE ON workflow_executions FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_immutable_workflow_execution_lineage()"
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_workflow_attempt_lineage() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id
             OR NEW.workflow_execution_id <> OLD.workflow_execution_id
             OR NEW.run_id <> OLD.run_id OR NEW.node_id <> OLD.node_id
             OR NEW.node_type <> OLD.node_type OR NEW.attempt <> OLD.attempt
             OR NEW.input_digest <> OLD.input_digest OR NEW.started_at <> OLD.started_at THEN
            RAISE EXCEPTION 'RunSigil workflow attempt lineage is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_immutable_workflow_attempt_lineage "
        "BEFORE UPDATE ON workflow_node_attempts FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_immutable_workflow_attempt_lineage()"
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_checkpoints_append_only "
        "BEFORE UPDATE OR DELETE ON run_checkpoints FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_append_only()"
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_evaluation_scenarios_append_only "
        "BEFORE UPDATE OR DELETE ON evaluation_scenarios FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_append_only()"
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_evaluation_results_append_only "
        "BEFORE UPDATE OR DELETE ON evaluation_results FOR EACH ROW "
        "EXECUTE FUNCTION runsigil_append_only()"
    )

    table_list = ", ".join(NEW_TENANT_TABLES)
    op.execute(
        f"GRANT SELECT ON {table_list} TO "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute(
        "GRANT INSERT, UPDATE ON workflows, workflow_versions, workflow_deployments, "
        "evaluation_datasets, evaluation_dataset_versions, evaluations TO runsigil_app"
    )
    op.execute(
        "GRANT INSERT ON workflow_executions, run_checkpoints, evaluation_scenarios "
        "TO runsigil_app"
    )
    op.execute(
        "GRANT UPDATE ON workflow_executions, workflow_node_attempts, evaluations "
        "TO runsigil_worker"
    )
    op.execute(
        "GRANT INSERT ON workflow_node_attempts, run_checkpoints, evaluation_results "
        "TO runsigil_worker"
    )
    op.execute(
        f"REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON {table_list} FROM "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE INSERT, UPDATE ON evaluation_results, workflow_node_attempts "
        "FROM runsigil_app, runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE INSERT, UPDATE ON workflows, workflow_versions, workflow_deployments, "
        "evaluation_datasets, evaluation_dataset_versions, evaluation_scenarios "
        "FROM runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE INSERT, UPDATE ON workflow_executions, run_checkpoints, evaluations "
        "FROM runsigil_gateway_authorizer"
    )
    op.execute("REVOKE UPDATE ON run_checkpoints FROM runsigil_app, runsigil_worker")
    op.execute("REVOKE UPDATE ON evaluation_scenarios FROM runsigil_app")
    op.execute("REVOKE INSERT ON evaluation_results FROM runsigil_app")

    for table in NEW_TENANT_TABLES:
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
        "0004 is intentionally irreversible because it preserves workflow and evaluation lineage"
    )
