from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient
from runsigil_control_api.main import app
from runsigil_control_api.seed import IDS

pytestmark = [pytest.mark.integration, pytest.mark.security]


def create_pending(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/v1/runs",
        headers=headers,
        json={
            "project_id": str(IDS["project"]),
            "environment_id": str(IDS["environment"]),
            "agent_id": str(IDS["agent"]),
            "recipient": "approval-target@example.test",
            "amount_cents": 2000,
            "description": "Approval binding test",
            "idempotency_key": f"approval-{secrets.token_urlsafe(10)}",
            "simulate_outcome": "committed",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_changed_digest_and_replay_are_rejected(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    with TestClient(app) as client:
        run = create_pending(client, api_headers)
        approval = run["approval"]
        wrong = client.post(
            f"/v1/approvals/{approval['id']}/decision",
            headers=api_headers,
            json={
                "content_digest": "sha256:" + "0" * 64,
                "decision": "approve",
                "reason": "attempted changed content",
            },
        )
        assert wrong.status_code == 409
        assert wrong.json()["code"] == "RUNSIGIL_APPROVAL_DIGEST_MISMATCH"

        approved = client.post(
            f"/v1/approvals/{approval['id']}/decision",
            headers=api_headers,
            json={
                "content_digest": approval["content_digest"],
                "decision": "approve",
                "reason": "exact content reviewed",
            },
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["action"]["state"] == "approved"

        replay = client.post(
            f"/v1/approvals/{approval['id']}/decision",
            headers=api_headers,
            json={
                "content_digest": approval["content_digest"],
                "decision": "approve",
                "reason": "replay",
            },
        )
        assert replay.status_code == 409
        assert replay.json()["code"] == "RUNSIGIL_APPROVAL_REPLAYED"
