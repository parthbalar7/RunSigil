from __future__ import annotations

import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from runsigil_contracts import ActionExecutionResult
from runsigil_control_api.main import app
from runsigil_control_api.models import (
    Action,
    AuditEvent,
    DeadLetter,
    Intent,
    OutboxEvent,
)
from runsigil_control_api.seed import IDS
from runsigil_control_api.services.budgets import action_reservations
from runsigil_control_api.services.governed_actions import database_now
from runsigil_worker.service import ActionWorker
from runsigil_worker.settings import WorkerSettings
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


def claim_ready_action(worker: ActionWorker, action_id: str):
    for _ in range(100):
        claim = worker.claim_ready()
        if claim is None:
            raise AssertionError("the expected ready action was not claimable")
        if str(claim.action_id) == action_id:
            return claim
        worker.settle(
            claim,
            ActionExecutionResult(outcome="failed", error_code="test_queue_cleanup"),
        )
    raise AssertionError("the expected ready action was not reached")


def claim_reconciliation_action(worker: ActionWorker, action_id: str):
    for _ in range(100):
        claim = worker.claim_reconciliation()
        if claim is None:
            raise AssertionError("the expected reconciliation was not claimable")
        if str(claim.action_id) == action_id:
            return claim
        worker.settle(
            claim,
            ActionExecutionResult(outcome="failed", error_code="test_queue_cleanup"),
        )
    raise AssertionError("the expected reconciliation was not reached")


def test_claim_commits_intent_and_outbox_before_effect_and_ambiguity_is_not_retried(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    run_id, action_id = approved_action(api_headers)
    worker = ActionWorker(engine=create_engine(database_urls["worker"]))
    claim = claim_ready_action(worker, action_id)

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


def test_ambiguous_effect_enters_bounded_dlq_and_redrives_only_reconciliation(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    run_id, action_id = approved_action(api_headers)
    settings = WorkerSettings(
        worker_database_url=database_urls["worker"],
        max_reconciliation_attempts=2,
        max_dlq_redrives=1,
        reconciliation_delay_seconds=1,
    )
    worker = ActionWorker(settings=settings, engine=create_engine(database_urls["worker"]))
    claim = claim_ready_action(worker, action_id)
    worker.settle(claim, ActionExecutionResult(outcome="ambiguous", error_code="unknown"))

    owner = create_engine(database_urls["owner"])

    def make_current_reconciliation_due() -> None:
        with Session(owner) as session, session.begin():
            now = database_now(session)
            actions = list(
                session.scalars(select(Action).where(Action.state == "reconciliation_required"))
            )
            for row in actions:
                row.next_reconcile_at = (
                    now - timedelta(seconds=1)
                    if str(row.id) == action_id
                    else now + timedelta(hours=1)
                )

    for expected_cycle in (1, 2):
        make_current_reconciliation_due()
        reconcile_claim = claim_reconciliation_action(worker, action_id)
        assert reconcile_claim.mode == "reconcile"
        worker.settle(
            reconcile_claim,
            ActionExecutionResult(outcome="ambiguous", error_code="still_unknown"),
        )
        with Session(owner) as session:
            action = session.get(Action, action_id)
            assert action is not None
            assert action.reconcile_cycle_attempts == expected_cycle

    with Session(owner) as session:
        action = session.get(Action, action_id)
        assert action is not None and action.state == "dead_lettered"
        dead_letter = session.scalar(select(DeadLetter).where(DeadLetter.action_id == action.id))
        assert dead_letter is not None and dead_letter.status == "open"
        assert dead_letter.max_redrives == 1
        reservations = action_reservations(
            session,
            organization_id=action.organization_id,
            action_id=action.id,
        )
        assert reservations and all(row.status == "active" for row in reservations)
        dead_letter_id = str(dead_letter.id)
        version = dead_letter.version

    with TestClient(app) as client:
        listed = client.get("/v1/dead-letters", headers=api_headers)
        assert listed.status_code == 200, listed.text
        assert dead_letter_id in {row["id"] for row in listed.json()["items"]}
        unauthorized = client.post(
            f"/v1/dead-letters/{dead_letter_id}/redrive",
            json={"expected_version": version, "reason": "unauthorized"},
        )
        assert unauthorized.status_code == 401
        redriven = client.post(
            f"/v1/dead-letters/{dead_letter_id}/redrive",
            headers=api_headers,
            json={"expected_version": version, "reason": "operator reconciliation"},
        )
        assert redriven.status_code == 200, redriven.text
        assert redriven.json()["status"] == "redriven"
        replay = client.post(
            f"/v1/dead-letters/{dead_letter_id}/redrive",
            headers=api_headers,
            json={
                "expected_version": redriven.json()["version"],
                "reason": "must not replay",
            },
        )
        assert replay.status_code == 409

    with Session(owner) as session:
        action = session.get(Action, action_id)
        assert action is not None and action.state == "reconciliation_required"
        assert action.reconcile_cycle_attempts == 0
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "dead_letter.redriven")
            .order_by(AuditEvent.sequence.desc())
        )
        assert audit is not None
        assert audit.metadata_json["reconcile_only"] is True
