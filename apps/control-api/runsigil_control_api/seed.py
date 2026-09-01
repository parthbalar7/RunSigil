from __future__ import annotations

import hashlib
import os
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from runsigil_contracts import canonical_digest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Agent,
    AgentVersion,
    AISystem,
    ApiKey,
    Budget,
    BudgetScope,
    Environment,
    ModelRoute,
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
    "model_route": UUID("60000000-0000-4000-8000-000000000002"),
}

BOOTSTRAP_SCOPES = [
    "context:read",
    "run:write",
    "run:read",
    "approval:read",
    "approval:decide",
    "evidence:read",
    "dlq:read",
    "dlq:redrive",
    "workflow:read",
    "workflow:write",
    "workflow:deploy",
    "workflow:run",
    "workflow:signal",
    "evaluation:read",
    "evaluation:write",
    "evaluation:run",
]

RESOURCE_LIMITS = {
    "currency:USD": 1_000,
    "tokens": 1_000_000,
    "requests": 10_000,
    "concurrent_runs": 100,
    "tool_actions": 10_000,
    "model_calls": 10_000,
}


def _stable_id(kind: str, value: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://runsigil.io/seed/{kind}/{value}")


def _policy_document() -> dict[str, Any]:
    return {
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
                "reason": "Production invoice delivery requires exact-content approval.",
            },
            {
                "id": "production-deterministic-workflow-node",
                "action_type": "workflow.node.execute",
                "environments": ["production"],
                "risks": ["low", "medium", "high", "critical"],
                "maximum_amount_minor": 0,
                "effect": "allow",
                "reason": "Deterministic workflow nodes are allowed inside the control plane.",
            },
        ],
    }


def _seed_milestone_two(session: Session) -> None:
    organization_id = IDS["organization"]
    route = session.get(ModelRoute, IDS["model_route"])
    if route is None:
        route = ModelRoute(
            id=IDS["model_route"],
            organization_id=organization_id,
            project_id=IDS["project"],
            name="demo-model-route",
            provider="demo",
            model="demo-governed-model",
            status="active",
        )
        session.add(route)
        session.flush()

    scope_targets: list[tuple[str, str, UUID | None]] = [
        ("organization", "organization", None),
        ("project", "project_id", IDS["project"]),
        ("environment", "environment_id", IDS["environment"]),
        ("agent", "agent_id", IDS["agent"]),
        ("user", "user_id", IDS["user"]),
        ("model_route", "model_route_id", IDS["model_route"]),
    ]
    scopes: list[BudgetScope] = []
    for scope_type, target_column, target_id in scope_targets:
        statement = session.query(BudgetScope).filter(BudgetScope.scope_type == scope_type)
        if target_id is not None:
            statement = statement.filter(getattr(BudgetScope, target_column) == target_id)
        scope = statement.one_or_none()
        if scope is None:
            values: dict[str, UUID | None] = {
                "project_id": None,
                "environment_id": None,
                "agent_id": None,
                "user_id": None,
                "model_route_id": None,
            }
            if target_id is not None:
                values[target_column] = target_id
            scope = BudgetScope(
                id=_stable_id("budget-scope", f"{scope_type}:{target_id or organization_id}"),
                organization_id=organization_id,
                scope_type=scope_type,
                **values,
            )
            session.add(scope)
            session.flush()
        scopes.append(scope)

    for scope in scopes:
        for resource_key, base_limit in RESOURCE_LIMITS.items():
            budget = (
                session.query(Budget)
                .filter(
                    Budget.budget_scope_id == scope.id,
                    Budget.resource_key == resource_key,
                )
                .one_or_none()
            )
            if budget is not None:
                budget.active = True
                continue
            budget_id = (
                IDS["budget"]
                if scope.scope_type == "project" and resource_key == "currency:USD"
                else _stable_id("budget", f"{scope.id}:{resource_key}")
            )
            session.add(
                Budget(
                    id=budget_id,
                    organization_id=organization_id,
                    budget_scope_id=scope.id,
                    resource_key=resource_key,
                    limit_value=base_limit,
                    reserved_value=0,
                    spent_value=0,
                    active=True,
                )
            )


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
                existing.scopes_json = BOOTSTRAP_SCOPES
            _seed_milestone_two(session)
            policy = session.get(PolicyBundle, IDS["policy"])
            if policy is not None:
                document = _policy_document()
                policy.document_json = document
                policy.content_digest = canonical_digest(document)
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
                scopes_json=BOOTSTRAP_SCOPES,
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
        policy_document = _policy_document()
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
        session.flush()
        _seed_milestone_two(session)


if __name__ == "__main__":
    seed()
