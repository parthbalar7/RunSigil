from __future__ import annotations

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from runsigil_control_api.main import app
from runsigil_control_api.models import Action, Budget, BudgetReservation, PolicyBundle, Run
from runsigil_control_api.seed import IDS
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def request_body(**overrides) -> dict:
    body = {
        "project_id": str(IDS["project"]),
        "environment_id": str(IDS["environment"]),
        "agent_id": str(IDS["agent"]),
        "recipient": "sensitive-recipient@example.test",
        "amount_cents": 4200,
        "description": "Approved invoice notification",
        "idempotency_key": f"test-{secrets.token_urlsafe(12)}",
        "simulate_outcome": "committed",
    }
    body.update(overrides)
    return body


def test_action_creates_durable_content_bound_approval(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    with TestClient(app) as client:
        response = client.post("/v1/runs", headers=api_headers, json=request_body())
    assert response.status_code == 202, response.text
    document = response.json()
    assert document["status"] == "waiting_for_approval"
    assert document["action"]["state"] == "proposed"
    assert document["approval"]["content_digest"] == document["action"]["content_digest"]
    assert "sensitive-recipient@example.test" not in json.dumps(document)

    owner = create_engine(database_urls["owner"])
    with Session(owner) as session:
        action = session.get(Action, document["action"]["id"])
        assert action is not None
        assert action.encrypted_arguments.startswith("rsenc1:")


def test_policy_outage_blocks_before_intent_and_action(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    owner = create_engine(database_urls["owner"])
    key = f"policy-outage-{secrets.token_urlsafe(8)}"
    with Session(owner) as session, session.begin():
        bundle = session.get(PolicyBundle, IDS["policy"])
        assert bundle is not None
        bundle.status = "disabled"
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/runs", headers=api_headers, json=request_body(idempotency_key=key)
            )
        assert response.status_code == 503
        assert response.json()["code"] == "RUNSIGIL_POLICY_UNAVAILABLE"
        with Session(owner) as session:
            action = session.scalar(
                select(Action).join(Run, Action.run_id == Run.id).where(Run.idempotency_key == key)
            )
            assert action is None
    finally:
        with Session(owner) as session, session.begin():
            bundle = session.get(PolicyBundle, IDS["policy"])
            assert bundle is not None
            bundle.status = "active"


def test_budget_exhaustion_blocks_before_action(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    owner = create_engine(database_urls["owner"])
    key = f"budget-exhausted-{secrets.token_urlsafe(8)}"
    with Session(owner) as session, session.begin():
        budget = session.get(Budget, IDS["budget"])
        assert budget is not None
        old_limit = budget.limit_minor
        budget.limit_minor = budget.spent_minor + budget.reserved_minor
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/runs", headers=api_headers, json=request_body(idempotency_key=key)
            )
        assert response.status_code == 409
        assert response.json()["code"] == "RUNSIGIL_BUDGET_EXHAUSTED"
    finally:
        with Session(owner) as session, session.begin():
            budget = session.get(Budget, IDS["budget"])
            assert budget is not None
            budget.limit_minor = old_limit


def test_runs_can_be_listed_and_cancelled_only_before_approval(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    with TestClient(app) as client:
        started = client.post("/v1/runs", headers=api_headers, json=request_body())
        assert started.status_code == 202, started.text
        run = started.json()

        listed = client.get(
            "/v1/runs",
            headers=api_headers,
            params={"status": "waiting_for_approval", "limit": 100},
        )
        assert listed.status_code == 200, listed.text
        page = listed.json()
        assert run["id"] in {item["id"] for item in page["items"]}
        assert page["total"] >= 1
        assert page["page_size"] == len(page["items"])

        cancelled = client.post(f"/v1/runs/{run['id']}/cancel", headers=api_headers)
        assert cancelled.status_code == 200, cancelled.text
        cancelled_run = cancelled.json()
        assert cancelled_run["status"] == "cancelled"
        assert cancelled_run["action"]["state"] == "rejected"
        assert cancelled_run["approval"]["status"] == "denied"
        assert cancelled_run["updated_at"] >= run["updated_at"]

        cancelled_page = client.get(
            "/v1/runs",
            headers=api_headers,
            params={"status": "cancelled", "terminal_kind": "cancelled", "limit": 100},
        )
        rejected_page = client.get(
            "/v1/runs",
            headers=api_headers,
            params={"status": "cancelled", "terminal_kind": "rejected", "limit": 100},
        )
        assert run["id"] in {item["id"] for item in cancelled_page.json()["items"]}
        assert run["id"] not in {item["id"] for item in rejected_page.json()["items"]}

        replay = client.post(f"/v1/runs/{run['id']}/cancel", headers=api_headers)
        assert replay.status_code == 409
        assert replay.json()["code"] == "RUNSIGIL_INVALID_TRANSITION"

    owner = create_engine(database_urls["owner"])
    with Session(owner) as session:
        action = session.get(Action, run["action"]["id"])
        assert action is not None
        reservation = session.get(BudgetReservation, action.budget_reservation_id)
        assert reservation is not None and reservation.status == "released"
