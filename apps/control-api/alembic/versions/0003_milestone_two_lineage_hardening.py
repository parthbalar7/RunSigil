"""Harden Milestone 2 lineage immutability and runtime grants.

Revision ID: 0003_m2_lineage
Revises: 0002_milestone_two
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op

revision = "0003_m2_lineage"
down_revision = "0002_milestone_two"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION runsigil_immutable_action_content() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id OR NEW.run_id <> OLD.run_id
             OR NEW.intent_id <> OLD.intent_id
             OR NEW.policy_decision_id <> OLD.policy_decision_id
             OR NEW.approval_request_id IS DISTINCT FROM OLD.approval_request_id
             OR NEW.budget_reservation_id <> OLD.budget_reservation_id
             OR NEW.tool_name <> OLD.tool_name
             OR NEW.content_digest <> OLD.content_digest
             OR NEW.encrypted_arguments <> OLD.encrypted_arguments
             OR NEW.provider_idempotency_key <> OLD.provider_idempotency_key THEN
            RAISE EXCEPTION 'RunSigil action authorization content is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "GRANT SELECT ON model_routes, budget_scopes, action_budget_reservations, "
        "dead_letters TO runsigil_app, runsigil_worker"
    )
    op.execute("GRANT INSERT ON action_budget_reservations TO runsigil_app")
    op.execute("GRANT UPDATE ON dead_letters TO runsigil_app")
    op.execute("GRANT INSERT, UPDATE ON dead_letters TO runsigil_worker")
    op.execute(
        "REVOKE INSERT, UPDATE ON model_routes, budget_scopes "
        "FROM runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE UPDATE ON action_budget_reservations FROM runsigil_app, runsigil_worker, "
        "runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE INSERT ON action_budget_reservations FROM runsigil_worker, "
        "runsigil_gateway_authorizer"
    )


def downgrade() -> None:
    raise RuntimeError("0003 is intentionally irreversible because it hardens effect lineage")
