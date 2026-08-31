from __future__ import annotations

import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from runsigil_control_api.main import app
from runsigil_control_api.models import (
    Action,
    ActionBudgetReservation,
    Budget,
    BudgetReservation,
    BudgetScope,
    ModelRoute,
    PolicyBundle,
    Run,
)
from runsigil_control_api.seed import IDS
from runsigil_control_api.services.budgets import (
    BudgetContext,
    link_action_reservations,
    reserve_budgets,
    settle_action_reservations,
)
from runsigil_control_api.services.governed_actions import database_now
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


def test_token_and_model_call_budgets_apply_to_every_scope(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    with TestClient(app) as client:
        response = client.post("/v1/runs", headers=api_headers, json=request_body())
    assert response.status_code == 202, response.text
    run = response.json()

    owner = create_engine(database_urls["owner"])
    with Session(owner) as session:
        route = session.get(ModelRoute, IDS["model_route"])
        assert route is not None
        reservations = reserve_budgets(
            session,
            context=BudgetContext(
                organization_id=IDS["organization"],
                project_id=IDS["project"],
                environment_id=IDS["environment"],
                agent_id=IDS["agent"],
                actor_id=IDS["user"],
                actor_type="user",
                model_route_id=route.id,
            ),
            run_id=UUID(run["id"]),
            estimates={"tokens": 250, "model_calls": 1},
            now=database_now(session),
        )
        session.flush()
        link_action_reservations(
            session,
            organization_id=IDS["organization"],
            action_id=UUID(run["action"]["id"]),
            reservations=reservations,
        )
        session.flush()
        scope_types = set(
            session.scalars(
                select(BudgetScope.scope_type)
                .join(Budget, Budget.budget_scope_id == BudgetScope.id)
                .join(BudgetReservation, BudgetReservation.budget_id == Budget.id)
                .where(BudgetReservation.id.in_([row.id for row in reservations]))
            )
        )
        assert len(reservations) == 12
        assert scope_types == {
            "organization",
            "project",
            "environment",
            "agent",
            "user",
            "model_route",
        }
        settled = settle_action_reservations(
            session,
            organization_id=IDS["organization"],
            action_id=UUID(run["action"]["id"]),
            now=database_now(session),
            committed=True,
            actual_usage={"tokens": 175, "model_calls": 1},
        )
        token_reservations = [row for row in settled if row.resource_key == "tokens"]
        assert len(token_reservations) == 6
        assert all(row.estimated_value == 250 for row in token_reservations)
        assert all(row.actual_value == 175 for row in token_reservations)
        session.rollback()


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
        old_limit = budget.limit_value
        budget.limit_value = budget.spent_value + budget.reserved_value
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
            budget.limit_value = old_limit


def test_budget_reservation_is_concurrency_safe(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    owner = create_engine(database_urls["owner"])
    with Session(owner) as session, session.begin():
        budget = session.scalar(
            select(Budget)
            .join(BudgetScope, BudgetScope.id == Budget.budget_scope_id)
            .where(
                BudgetScope.scope_type == "agent",
                BudgetScope.agent_id == IDS["agent"],
                Budget.resource_key == "tool_actions",
            )
        )
        assert budget is not None
        old_limit = budget.limit_value
        budget.limit_value = budget.spent_value + budget.reserved_value + 1

    def start(index: int) -> tuple[int, dict]:
        with TestClient(app) as client:
            response = client.post(
                "/v1/runs",
                headers=api_headers,
                json=request_body(
                    idempotency_key=f"concurrent-budget-{index}-{secrets.token_hex(6)}"
                ),
            )
            return response.status_code, response.json()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(start, (1, 2)))
        assert sorted(status for status, _body in results) == [202, 409]
        rejected = next(body for status, body in results if status == 409)
        assert rejected["code"] == "RUNSIGIL_BUDGET_EXHAUSTED"
        accepted = next(body for status, body in results if status == 202)
        with TestClient(app) as client:
            cancelled = client.post(f"/v1/runs/{accepted['id']}/cancel", headers=api_headers)
            assert cancelled.status_code == 200, cancelled.text
    finally:
        with Session(owner) as session, session.begin():
            budget = session.scalar(
                select(Budget)
                .join(BudgetScope, BudgetScope.id == Budget.budget_scope_id)
                .where(
                    BudgetScope.scope_type == "agent",
                    BudgetScope.agent_id == IDS["agent"],
                    Budget.resource_key == "tool_actions",
                )
            )
            assert budget is not None
            budget.limit_value = old_limit


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
        reservations = list(
            session.scalars(
                select(BudgetReservation)
                .join(
                    ActionBudgetReservation,
                    ActionBudgetReservation.budget_reservation_id == BudgetReservation.id,
                )
                .where(ActionBudgetReservation.action_id == action.id)
            )
        )
        assert len(reservations) == 20
        assert {row.resource_key for row in reservations} == {
            "currency:USD",
            "requests",
            "concurrent_runs",
            "tool_actions",
        }
        assert all(row.status == "released" for row in reservations)
