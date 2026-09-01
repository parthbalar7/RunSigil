"""Milestone 0 foundation and governed-action vertical slice.

Revision ID: 0001_milestone_one
Revises: None
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op

from runsigil_control_api.models import Base

revision = "0001_milestone_one"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "users",
    "service_identities",
    "workload_identities",
    "api_keys",
    "delegations",
    "projects",
    "environments",
    "ai_systems",
    "agents",
    "agent_versions",
    "tools",
    "policy_bundles",
    "policy_decisions",
    "budgets",
    "budget_reservations",
    "approval_requests",
    "runs",
    "intents",
    "actions",
    "outbox_events",
    "trace_events",
    "audit_events",
    "evidence_bundles",
]

# Keep this historical migration independent of models added in later milestones.
# The Milestone 2 tables were already part of the repository metadata when this
# baseline was cut and remain here for compatibility with its guarded 0002 upgrade.
FOUNDATION_TABLES = [
    "organizations",
    *TENANT_TABLES,
    "model_routes",
    "budget_scopes",
    "action_budget_reservations",
    "dead_letters",
]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind,
        tables=[Base.metadata.tables[name] for name in FOUNDATION_TABLES],
    )

    op.execute(
        "GRANT USAGE ON SCHEMA public TO "
        "runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO runsigil_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO runsigil_worker")
    op.execute(
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO runsigil_gateway_authorizer"
    )
    op.execute(
        "REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public "
        "FROM runsigil_app, runsigil_worker, runsigil_gateway_authorizer"
    )
    op.execute("REVOKE INSERT, UPDATE ON api_keys FROM runsigil_app")
    op.execute("REVOKE INSERT, UPDATE ON evidence_bundles FROM runsigil_app")
    op.execute("REVOKE UPDATE ON audit_events FROM runsigil_app")

    op.execute(
        "ALTER TABLE runs ADD CONSTRAINT ck_runs_status CHECK "
        "(status IN ('authorizing','waiting_for_approval','queued','running','completed','failed','cancelled','reconciliation_required'))"
    )
    op.execute(
        "ALTER TABLE actions ADD CONSTRAINT ck_actions_state CHECK "
        "(state IN ('proposed','approved','executing','reconciling','committed','failed','rejected','reconciliation_required'))"
    )
    op.execute(
        "ALTER TABLE approval_requests ADD CONSTRAINT ck_approval_requests_status CHECK "
        "(status IN ('pending','approved','denied','expired'))"
    )
    op.execute(
        "ALTER TABLE budget_reservations ADD CONSTRAINT ck_budget_reservations_status CHECK "
        "(status IN ('active','committed','released','expired'))"
    )

    op.execute(
        """
        CREATE FUNCTION runsigil_request_organization() RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp AS $$
          SELECT organization_id
          FROM public.api_keys
          WHERE key_hash = nullif(current_setting('runsigil.api_key_hash', true), '')
            AND active = true
            AND (expires_at IS NULL OR expires_at > clock_timestamp())
          LIMIT 1
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION runsigil_request_organization() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION runsigil_request_organization() TO runsigil_app")

    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY runsigil_org_tenant_policy ON organizations TO runsigil_app "
        "USING (id = runsigil_request_organization()) "
        "WITH CHECK (id = runsigil_request_organization())"
    )
    op.execute(
        "CREATE POLICY runsigil_org_worker_policy ON organizations TO runsigil_worker "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY runsigil_org_gateway_policy ON organizations "
        "TO runsigil_gateway_authorizer USING (true)"
    )

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        if table == "api_keys":
            op.execute(
                "CREATE POLICY runsigil_api_keys_tenant_policy ON api_keys TO runsigil_app "
                "USING (key_hash = nullif(current_setting('runsigil.api_key_hash', true), '')) "
                "WITH CHECK (organization_id = runsigil_request_organization())"
            )
        else:
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

    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_intent() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id OR NEW.run_id <> OLD.run_id
             OR NEW.actor_id <> OLD.actor_id OR NEW.delegation_id <> OLD.delegation_id
             OR NEW.action_type <> OLD.action_type OR NEW.arguments_digest <> OLD.arguments_digest
             OR NEW.content_digest <> OLD.content_digest OR NEW.idempotency_key <> OLD.idempotency_key THEN
            RAISE EXCEPTION 'RunSigil intent content is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_immutable_intent BEFORE UPDATE ON intents "
        "FOR EACH ROW EXECUTE FUNCTION runsigil_immutable_intent()"
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_immutable_action_content() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id OR NEW.run_id <> OLD.run_id
             OR NEW.intent_id <> OLD.intent_id OR NEW.content_digest <> OLD.content_digest
             OR NEW.encrypted_arguments <> OLD.encrypted_arguments
             OR NEW.provider_idempotency_key <> OLD.provider_idempotency_key THEN
            RAISE EXCEPTION 'RunSigil action authorization content is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_immutable_action_content BEFORE UPDATE ON actions "
        "FOR EACH ROW EXECUTE FUNCTION runsigil_immutable_action_content()"
    )
    op.execute(
        """
        CREATE FUNCTION runsigil_append_only() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          RAISE EXCEPTION 'RunSigil evidence and audit rows are append-only';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_audit_append_only BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION runsigil_append_only()"
    )
    op.execute(
        "CREATE TRIGGER trg_runsigil_evidence_append_only BEFORE UPDATE OR DELETE ON evidence_bundles "
        "FOR EACH ROW EXECUTE FUNCTION runsigil_append_only()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP FUNCTION IF EXISTS runsigil_append_only() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS runsigil_immutable_action_content() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS runsigil_immutable_intent() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS runsigil_request_organization() CASCADE")
    Base.metadata.drop_all(bind=bind)
