from __future__ import annotations

import hashlib
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from runsigil_control_api.main import app
from sqlalchemy import create_engine, text

pytestmark = [pytest.mark.integration, pytest.mark.security]

ORG_B = UUID("90000000-0000-4000-8000-000000000001")
PROJECT_B = UUID("90000000-0000-4000-8000-000000000002")
ENV_B = UUID("90000000-0000-4000-8000-000000000003")
SYSTEM_B = UUID("90000000-0000-4000-8000-000000000004")
WORKLOAD_B = UUID("90000000-0000-4000-8000-000000000005")
AGENT_B = UUID("90000000-0000-4000-8000-000000000006")
RUN_B = UUID("90000000-0000-4000-8000-000000000007")


def seed_org_b(owner_url: str) -> None:
    engine = create_engine(owner_url)
    digest = "sha256:" + "9" * 64
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, slug, name) "
                "VALUES (:id, 'org-b', 'Organization B') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": ORG_B},
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, organization_id, slug, name) "
                "VALUES (:id, :org, 'private', 'Private') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": PROJECT_B, "org": ORG_B},
        )
        connection.execute(
            text(
                "INSERT INTO environments "
                "(id, organization_id, slug, name, environment_type, protected) "
                "VALUES (:id, :org, 'production', 'Production', 'production', true) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": ENV_B, "org": ORG_B},
        )
        connection.execute(
            text(
                "INSERT INTO workload_identities "
                "(id, organization_id, name, subject, active) "
                "VALUES (:id, :org, 'private workload', "
                "'runsigil:workload:private', true) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": WORKLOAD_B, "org": ORG_B},
        )
        connection.execute(
            text(
                "INSERT INTO ai_systems "
                "(id, organization_id, project_id, name, owner, risk_tier) "
                "VALUES (:id, :org, :project, 'Private system', 'Private', 'high') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": SYSTEM_B, "org": ORG_B, "project": PROJECT_B},
        )
        connection.execute(
            text(
                "INSERT INTO agents "
                "(id, organization_id, system_id, name, framework, workload_identity_id) "
                "VALUES (:id, :org, :system, 'Private agent', 'external', :workload) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": AGENT_B,
                "org": ORG_B,
                "system": SYSTEM_B,
                "workload": WORKLOAD_B,
            },
        )
        connection.execute(
            text(
                "INSERT INTO runs "
                "(id, organization_id, project_id, environment_id, agent_id, actor_id, "
                "status, idempotency_key, input_digest) "
                "VALUES (:id, :org, :project, :env, :agent, :actor, 'queued', "
                "'org-b-private-run', :digest) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": RUN_B,
                "org": ORG_B,
                "project": PROJECT_B,
                "env": ENV_B,
                "agent": AGENT_B,
                "actor": UUID("90000000-0000-4000-8000-000000000008"),
                "digest": digest,
            },
        )


def test_org_a_cannot_read_org_b_through_api_or_direct_app_role(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    seed_org_b(database_urls["owner"])
    with TestClient(app) as client:
        response = client.get(f"/v1/runs/{RUN_B}", headers=api_headers)
        listed = client.get("/v1/runs", headers=api_headers, params={"limit": 100})
        cancelled = client.post(f"/v1/runs/{RUN_B}/cancel", headers=api_headers)
    assert response.status_code == 404
    assert listed.status_code == 200
    assert str(RUN_B) not in {row["id"] for row in listed.json()["items"]}
    assert cancelled.status_code == 404

    api_key_hash = hashlib.sha256(
        api_headers["Authorization"].removeprefix("Bearer ").encode()
    ).hexdigest()
    app_engine = create_engine(database_urls["app"])
    with app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('runsigil.api_key_hash', :key_hash, true)"),
            {"key_hash": api_key_hash},
        )
        connection.execute(
            text("SELECT set_config('runsigil.organization_id', :org_b, true)"),
            {"org_b": str(ORG_B)},
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM runs WHERE id = :id"), {"id": RUN_B}) == 0
        )
        result = connection.execute(
            text("UPDATE runs SET status = 'cancelled' WHERE id = :id"), {"id": RUN_B}
        )
        assert result.rowcount == 0
