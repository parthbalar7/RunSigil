from __future__ import annotations

import hashlib
import os
from uuid import UUID

from runsigil_contracts import canonical_digest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Agent,
    AgentVersion,
    AISystem,
    ApiKey,
    Budget,
    Environment,
    Organization,
    PolicyBundle,
    Project,
    ServiceIdentity,
    Tool,
    User,
    WorkloadIdentity,
)
from runsigil_control_api.settings import get_migration_settings

IDS = {
    "organization": UUID("10000000-0000-4000-8000-000000000001"),
    "user": UUID("10000000-0000-4000-8000-000000000002"),
    "api_key": UUID("10000000-0000-4000-8000-000000000003"),
    "service": UUID("10000000-0000-4000-8000-000000000004"),
    "workload": UUID("10000000-0000-4000-8000-000000000005"),
    "project": UUID("20000000-0000-4000-8000-000000000001"),
    "environment": UUID("30000000-0000-4000-8000-000000000001"),
    "system": UUID("40000000-0000-4000-8000-000000000001"),
    "agent": UUID("50000000-0000-4000-8000-000000000001"),
    "agent_version": UUID("50000000-0000-4000-8000-000000000002"),
    "tool": UUID("60000000-0000-4000-8000-000000000001"),
    "policy": UUID("70000000-0000-4000-8000-000000000001"),
    "budget": UUID("80000000-0000-4000-8000-000000000001"),
}


def seed() -> None:
    settings = get_migration_settings()
    api_key = os.environ.get("RUNSIGIL_BOOTSTRAP_API_KEY", "")
    if len(api_key) < 20:
        raise RuntimeError("RUNSIGIL_BOOTSTRAP_API_KEY must contain at least 20 characters")
    engine = create_engine(settings.migration_database_url, pool_pre_ping=True)
    with Session(engine) as session, session.begin():
        if session.get(Organization, IDS["organization"]) is not None:
            existing = session.get(ApiKey, IDS["api_key"])
            if existing is not None:
                existing.key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            return
        organization_id = IDS["organization"]
        session.add(Organization(id=organization_id, slug="sigil-labs", name="Sigil Labs"))
        session.flush()
        session.add(
            User(
                id=IDS["user"],
                organization_id=organization_id,
                email="operator@example.test",
                display_name="Demo Operator",
                active=True,
            )
        )
        session.add(
            ApiKey(
                id=IDS["api_key"],
                organization_id=organization_id,
                name="local bootstrap",
                key_hash=hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
                actor_id=IDS["user"],
                actor_type="user",
                scopes_json=[
                    "context:read",
                    "run:write",
                    "run:read",
                    "approval:read",
                    "approval:decide",
                    "evidence:read",
                ],
                active=True,
            )
        )
        session.add(
            ServiceIdentity(
                id=IDS["service"],
                organization_id=organization_id,
                name="demo provider gateway identity",
                audience=settings.demo_provider_audience,
                active=True,
            )
        )
        session.add(
            WorkloadIdentity(
                id=IDS["workload"],
                organization_id=organization_id,
                name="invoice assistant workload",
                subject="runsigil:workload:invoice-assistant",
                active=True,
            )
        )
        session.add(
            Project(
                id=IDS["project"],
                organization_id=organization_id,
                slug="operations",
                name="Operations",
            )
        )
        session.add(
            Environment(
                id=IDS["environment"],
                organization_id=organization_id,
                slug="production",
                name="Production",
                environment_type="production",
                protected=True,
            )
        )
        session.flush()
        session.add(
            AISystem(
                id=IDS["system"],
                organization_id=organization_id,
                project_id=IDS["project"],
                name="Invoice Operations",
                owner="FinOps",
                risk_tier="high",
            )
        )
        session.flush()
        session.add(
            Agent(
                id=IDS["agent"],
                organization_id=organization_id,
                system_id=IDS["system"],
                name="Invoice Assistant",
                framework="external-supervised",
                workload_identity_id=IDS["workload"],
            )
        )
        session.flush()
        session.add(
            AgentVersion(
                id=IDS["agent_version"],
                organization_id=organization_id,
                agent_id=IDS["agent"],
                version=1,
                config_digest=canonical_digest({"agent": "invoice-assistant", "version": 1}),
                status="active",
            )
        )
        session.add(
            Tool(
                id=IDS["tool"],
                organization_id=organization_id,
                name="demo.invoice.send",
                effect_class="transactional",
                risk="high",
                connector="runsigil-demo-provider-v1",
                input_schema_json={
                    "type": "object",
                    "required": ["recipient", "amount_cents", "description"],
                },
            )
        )
        policy_document = {
            "schema_version": 1,
            "enabled": True,
            "valid_until": None,
            "default_effect": "deny",
            "rules": [
                {
                    "id": "production-invoice-requires-approval",
                    "action_type": "demo.invoice.send",
                    "environments": ["production"],
                    "risks": ["high"],
                    "maximum_amount_minor": 100_000,
                    "effect": "require_approval",
                    "reason": (
                        "Production invoice delivery requires an exact-content human approval."
                    ),
                }
            ],
        }
        session.add(
            PolicyBundle(
                id=IDS["policy"],
                organization_id=organization_id,
                project_id=IDS["project"],
                name="Production action policy",
                status="active",
                document_json=policy_document,
                content_digest=canonical_digest(policy_document),
            )
        )
        session.add(
            Budget(
                id=IDS["budget"],
                organization_id=organization_id,
                project_id=IDS["project"],
                currency="USD",
                limit_minor=1_000,
                reserved_minor=0,
                spent_minor=0,
            )
        )


if __name__ == "__main__":
    seed()
