from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient
from runsigil_contracts import ActionExecutionResult
from runsigil_control_api.main import app
from runsigil_control_api.models import Action, Intent, OutboxEvent
from runsigil_control_api.seed import IDS
from runsigil_worker.service import ActionWorker
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def approved_action(api_headers: dict[str, str]) -> tuple[str, str]:
    with TestClient(app) as client:
        started = client.post(
            "/v1/runs",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
                "recipient": "worker-test@example.test",
                "amount_cents": 1000,
                "description": "Worker durability test",
                "idempotency_key": f"worker-{secrets.token_urlsafe(10)}",
                "simulate_outcome": "committed",
            },
        )
        assert started.status_code == 202, started.text
        run = started.json()
        approval = run["approval"]
        decided = client.post(
            f"/v1/approvals/{approval['id']}/decision",
            headers=api_headers,
            json={
                "content_digest": approval["content_digest"],
                "decision": "approve",
                "reason": "worker test",
            },
        )
        assert decided.status_code == 200, decided.text
        return run["id"], run["action"]["id"]


def test_claim_commits_intent_and_outbox_before_effect_and_ambiguity_is_not_retried(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    run_id, action_id = approved_action(api_headers)
    worker = ActionWorker(engine=create_engine(database_urls["worker"]))
    claim = worker.claim_ready()
    assert claim is not None
    assert str(claim.action_id) == action_id

    owner = create_engine(database_urls["owner"])
    with Session(owner) as session:
        action = session.get(Action, action_id)
        assert action is not None and action.state == "executing"
        assert session.get(Intent, action.intent_id) is not None
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == action.id))
        assert outbox is not None and outbox.dispatched_at is not None

    worker.settle(
        claim,
        ActionExecutionResult(outcome="ambiguous", error_code="simulated_timeout"),
    )
    with Session(owner) as session:
        action = session.get(Action, action_id)
        assert action is not None
        assert action.state == "reconciliation_required"
        assert action.execute_attempts == 1
        assert action.reconcile_attempts == 0
