"""Milestone 2 budgets, dead letters, and trace correlation.

Revision ID: 0002_milestone_two
Revises: 0001_milestone_one
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

from runsigil_control_api.models import Base

revision = "0002_milestone_two"
down_revision = "0001_milestone_one"
branch_labels = None
depends_on = None

NEW_TENANT_TABLES = [
    "model_routes",
    "budget_scopes",
    "action_budget_reservations",
    "dead_letters",
]


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # 0001 intentionally creates its frozen foundation from metadata. Creating
    # these named tables with checkfirst keeps both existing and fresh upgrades
    # safe while the data transforms below handle the legacy budget shape.
    for table_name in NEW_TENANT_TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)

    budget_columns = _columns("budgets")
    if "budget_scope_id" not in budget_columns:
        op.execute("ALTER TABLE budgets ADD COLUMN budget_scope_id uuid")
        op.execute("ALTER TABLE budgets ADD COLUMN resource_key varchar(100)")
        op.execute("ALTER TABLE budgets ADD COLUMN limit_value bigint")
        op.execute("ALTER TABLE budgets ADD COLUMN reserved_value bigint")
        op.execute("ALTER TABLE budgets ADD COLUMN spent_value bigint")
        op.execute("ALTER TABLE budgets ADD COLUMN active boolean")
        op.execute(
            """
            INSERT INTO budget_scopes (
                id, organization_id, scope_type, project_id, environment_id,
                agent_id, user_id, model_route_id, created_at, updated_at
            )
            SELECT DISTINCT
                (
                    substr(md5(organization_id::text || ':project:' || project_id::text), 1, 8)
                    || '-' || substr(md5(organization_id::text || ':project:' || project_id::text), 9, 4)
                    || '-4' || substr(md5(organization_id::text || ':project:' || project_id::text), 14, 3)
                    || '-8' || substr(md5(organization_id::text || ':project:' || project_id::text), 18, 3)
                    || '-' || substr(md5(organization_id::text || ':project:' || project_id::text), 21, 12)
                )::uuid,
                organization_id, 'project', project_id, NULL::uuid, NULL::uuid,
                NULL::uuid, NULL::uuid,
                current_timestamp, current_timestamp
            FROM budgets
            """
        )
        op.execute(
            """
            UPDATE budgets AS budget
            SET budget_scope_id = scope.id,
                resource_key = 'currency:' || budget.currency,
                limit_value = budget.limit_minor,
                reserved_value = budget.reserved_minor,
                spent_value = budget.spent_minor,
                active = true
            FROM budget_scopes AS scope
            WHERE scope.organization_id = budget.organization_id
              AND scope.scope_type = 'project'
              AND scope.project_id = budget.project_id
            """
        )
        for column in (
            "budget_scope_id",
            "resource_key",
            "limit_value",
            "reserved_value",
            "spent_value",
            "active",
        ):
            op.execute(f"ALTER TABLE budgets ALTER COLUMN {column} SET NOT NULL")
        for legacy_column in (
            "project_id",
            "currency",
            "limit_minor",
            "reserved_minor",
            "spent_minor",
        ):
            op.execute(f"ALTER TABLE budgets ALTER COLUMN {legacy_column} DROP NOT NULL")

    reservation_columns = _columns("budget_reservations")
    if "resource_key" not in reservation_columns:
        op.execute("ALTER TABLE budget_reservations ADD COLUMN resource_key varchar(100)")
        op.execute("ALTER TABLE budget_reservations ADD COLUMN estimated_value bigint")
        op.execute("ALTER TABLE budget_reservations ADD COLUMN actual_value bigint")
        op.execute(
            """
            UPDATE budget_reservations
            SET resource_key = 'currency:' || currency,
                estimated_value = amount_minor,
                actual_value = CASE WHEN status = 'committed' THEN amount_minor ELSE NULL END
            """
        )
        op.execute("ALTER TABLE budget_reservations ALTER COLUMN resource_key SET NOT NULL")
        op.execute("ALTER TABLE budget_reservations ALTER COLUMN estimated_value SET NOT NULL")
        op.execute("ALTER TABLE budget_reservations ALTER COLUMN amount_minor DROP NOT NULL")
        op.execute("ALTER TABLE budget_reservations ALTER COLUMN currency DROP NOT NULL")

    action_columns = _columns("actions")
    if "reconcile_cycle_attempts" not in action_columns:
        op.execute(
            "ALTER TABLE actions ADD COLUMN reconcile_cycle_attempts integer NOT NULL DEFAULT 0"
        )

    trace_columns = _columns("trace_events")
    if "trace_id" not in trace_columns:
        op.execute("ALTER TABLE trace_events ADD COLUMN trace_id varchar(32)")
        op.execute("UPDATE trace_events SET trace_id = md5(run_id::text)")
        op.execute("ALTER TABLE trace_events ALTER COLUMN trace_id SET NOT NULL")

    op.execute(
        """
        INSERT INTO action_budget_reservations (
            organization_id, action_id, budget_reservation_id, created_at, updated_at
        )
        SELECT organization_id, id, budget_reservation_id, current_timestamp, current_timestamp
        FROM actions
        ON CONFLICT DO NOTHING
        """
    )

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
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'fk_budgets_scope_tenant'
          ) THEN
            ALTER TABLE budgets ADD CONSTRAINT fk_budgets_scope_tenant
              FOREIGN KEY (organization_id, budget_scope_id)
              REFERENCES budget_scopes (organization_id, id) ON DELETE RESTRICT;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_budgets_scope_resource'
          ) THEN
            ALTER TABLE budgets ADD CONSTRAINT uq_budgets_scope_resource
              UNIQUE (organization_id, budget_scope_id, resource_key);
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_budgets_nonnegative'
          ) THEN
            ALTER TABLE budgets ADD CONSTRAINT ck_budgets_nonnegative
              CHECK (limit_value >= 0 AND reserved_value >= 0 AND spent_value >= 0);
          END IF;
        END $$
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_scope_organization "
        "ON budget_scopes (organization_id) WHERE scope_type = 'organization'"
    )
    for scope_type, column in (
        ("project", "project_id"),
        ("environment", "environment_id"),
        ("agent", "agent_id"),
        ("user", "user_id"),
        ("model_route", "model_route_id"),
    ):
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_scope_{scope_type} "
            f"ON budget_scopes (organization_id, {column}) "
            f"WHERE scope_type = '{scope_type}'"
        )

    op.execute("ALTER TABLE runs DROP CONSTRAINT IF EXISTS ck_runs_status")
    op.execute(
        "ALTER TABLE runs ADD CONSTRAINT ck_runs_status CHECK "
        "(status IN ('authorizing','waiting_for_approval','queued','running','completed',"
        "'failed','cancelled','reconciliation_required','dead_lettered'))"
    )
    op.execute("ALTER TABLE actions DROP CONSTRAINT IF EXISTS ck_actions_state")
    op.execute(
        "ALTER TABLE actions ADD CONSTRAINT ck_actions_state CHECK "
        "(state IN ('proposed','approved','executing','reconciling','committed','failed',"
        "'rejected','reconciliation_required','dead_lettered'))"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_dead_letters_status'
          ) THEN
            ALTER TABLE dead_letters ADD CONSTRAINT ck_dead_letters_status
              CHECK (status IN ('open','redriven','resolved'));
          END IF;
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
    op.execute(
        "GRANT SELECT ON model_routes, budget_scopes, action_budget_reservations, dead_letters "
        "TO runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON model_routes, budget_scopes, "
        "action_budget_reservations, dead_letters FROM runsigil_app, runsigil_worker, "
        "runsigil_gateway_authorizer"
    )
    for table in NEW_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS runsigil_{table}_tenant_policy ON {table}")
        op.execute(
            f"CREATE POLICY runsigil_{table}_tenant_policy ON {table} TO runsigil_app "
            "USING (organization_id = runsigil_request_organization()) "
            "WITH CHECK (organization_id = runsigil_request_organization())"
        )
        op.execute(f"DROP POLICY IF EXISTS runsigil_{table}_worker_policy ON {table}")
        op.execute(
            f"CREATE POLICY runsigil_{table}_worker_policy ON {table} TO runsigil_worker "
            "USING (true) WITH CHECK (true)"
        )
        op.execute(f"DROP POLICY IF EXISTS runsigil_{table}_gateway_policy ON {table}")
        op.execute(
            f"CREATE POLICY runsigil_{table}_gateway_policy ON {table} "
            "TO runsigil_gateway_authorizer USING (true)"
        )


def downgrade() -> None:
    # Deliberately preserve budget/effect lineage on downgrade. Operators may
    # remove the additive Milestone 2 tables only after an explicit data export.
    raise RuntimeError("0002 is intentionally irreversible because it preserves effect lineage")
