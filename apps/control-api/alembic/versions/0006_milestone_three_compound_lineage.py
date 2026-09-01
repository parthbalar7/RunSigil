"""Strengthen compound workflow execution lineage.

Revision ID: 0006_m3_lineage
Revises: 0005_m3_json_trigger
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0006_m3_lineage"
down_revision = "0005_m3_json_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    result_columns = {
        column["name"] for column in inspect(bind).get_columns("evaluation_results")
    }
    if "workflow_execution_id" not in result_columns:
        op.execute("ALTER TABLE evaluation_results ADD COLUMN workflow_execution_id uuid")
        op.execute(
            "ALTER TABLE evaluation_results DISABLE TRIGGER "
            "trg_runsigil_evaluation_results_append_only"
        )
        op.execute(
            "UPDATE evaluation_results AS result SET workflow_execution_id = execution.id "
            "FROM workflow_executions AS execution "
            "WHERE execution.organization_id = result.organization_id "
            "AND execution.run_id = result.run_id"
        )
        op.execute(
            "ALTER TABLE evaluation_results ENABLE TRIGGER "
            "trg_runsigil_evaluation_results_append_only"
        )
        op.execute(
            "DO $$ BEGIN IF EXISTS ("
            "SELECT 1 FROM evaluation_results WHERE workflow_execution_id IS NULL"
            ") THEN RAISE EXCEPTION "
            "'cannot harden evaluation lineage: an execution mapping is missing'; "
            "END IF; END $$"
        )
        op.execute(
            "ALTER TABLE evaluation_results ALTER COLUMN workflow_execution_id SET NOT NULL"
        )

    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_workflow_execution_identity_run'
          ) THEN
            ALTER TABLE workflow_executions
              ADD CONSTRAINT uq_workflow_execution_identity_run
              UNIQUE (organization_id, id, run_id);
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_workflow_execution_eval_lineage'
          ) THEN
            ALTER TABLE workflow_executions
              ADD CONSTRAINT uq_workflow_execution_eval_lineage
              UNIQUE (
                organization_id, id, evaluation_id, evaluation_scenario_id, run_id
              );
          END IF;
        END $$
        """
    )

    op.execute(
        "ALTER TABLE workflow_node_attempts DROP CONSTRAINT IF EXISTS "
        "fk_workflow_node_attempts_organization_id_workflow_executions"
    )
    op.execute(
        "ALTER TABLE workflow_node_attempts DROP CONSTRAINT IF EXISTS "
        "fk_workflow_node_attempts_organization_id_runs"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_workflow_node_attempt_execution_run'
          ) THEN
            ALTER TABLE workflow_node_attempts
              ADD CONSTRAINT fk_workflow_node_attempt_execution_run
              FOREIGN KEY (organization_id, workflow_execution_id, run_id)
              REFERENCES workflow_executions (organization_id, id, run_id)
              ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )

    op.execute(
        "ALTER TABLE run_checkpoints DROP CONSTRAINT IF EXISTS "
        "fk_run_checkpoints_organization_id_workflow_executions"
    )
    op.execute(
        "ALTER TABLE run_checkpoints DROP CONSTRAINT IF EXISTS "
        "fk_run_checkpoints_organization_id_runs"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_run_checkpoint_execution_run'
          ) THEN
            ALTER TABLE run_checkpoints
              ADD CONSTRAINT fk_run_checkpoint_execution_run
              FOREIGN KEY (organization_id, workflow_execution_id, run_id)
              REFERENCES workflow_executions (organization_id, id, run_id)
              ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )

    op.execute(
        "ALTER TABLE evaluation_results DROP CONSTRAINT IF EXISTS "
        "fk_evaluation_results_organization_id_runs"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_eval_result_execution_lineage'
          ) THEN
            ALTER TABLE evaluation_results
              ADD CONSTRAINT fk_eval_result_execution_lineage
              FOREIGN KEY (
                organization_id, workflow_execution_id, evaluation_id, scenario_id, run_id
              ) REFERENCES workflow_executions (
                organization_id, id, evaluation_id, evaluation_scenario_id, run_id
              ) ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    raise RuntimeError("0006 is intentionally irreversible because it hardens workflow lineage")
