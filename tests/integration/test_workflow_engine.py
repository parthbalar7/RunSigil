from __future__ import annotations

import json
import os
import secrets
import time
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from runsigil_contracts import (
    ActionExecutionResult,
    ModelExecutionResult,
    canonical_digest,
)
from runsigil_control_api.main import app
from runsigil_control_api.models import (
    Action,
    DeadLetter,
    EvaluationAnnotation,
    EvaluationScenario,
    ModelCall,
    ModelCallBudgetReservation,
    OutboxEvent,
    Tool,
    WorkflowExecution,
    WorkflowPolicyDecision,
    WorkflowReplay,
    WorkflowSubworkflowCall,
    WorkflowToolCall,
    WorkflowWait,
)
from runsigil_control_api.seed import IDS
from runsigil_control_api.services.governed_actions import database_now
from runsigil_control_api.services.workflow_engine import WorkflowEngineWorker
from runsigil_control_api.services.workflow_tools import tool_document
from runsigil_worker.service import ActionWorker
from runsigil_worker.settings import WorkerSettings
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _workflow_definition() -> dict[str, Any]:
    condition = {"field": "approved", "operator": "eq", "value": True}
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {"id": "route", "type": "condition", "name": "Approval route", "config": condition},
            {"id": "fan", "type": "parallel", "name": "Parallel checks"},
            {"id": "left", "type": "condition", "name": "Left check", "config": condition},
            {"id": "right", "type": "condition", "name": "Right check", "config": condition},
            {"id": "join", "type": "join", "name": "Deterministic join"},
            {
                "id": "loop",
                "type": "bounded_loop",
                "name": "Bounded review",
                "config": {
                    "max_iterations": 2,
                    "max_duration_seconds": 30,
                    "max_tokens": 100,
                    "max_cost_minor": 10,
                },
            },
            {"id": "body", "type": "condition", "name": "Review body", "config": condition},
            {"id": "accepted", "type": "output", "name": "Accepted"},
            {"id": "rejected", "type": "output", "name": "Rejected"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "route"},
            {"id": "e2", "source": "route", "target": "fan", "branch": "true"},
            {"id": "e3", "source": "route", "target": "rejected", "branch": "false"},
            {"id": "e4", "source": "fan", "target": "left"},
            {"id": "e5", "source": "fan", "target": "right"},
            {"id": "e6", "source": "left", "target": "join", "branch": "true"},
            {"id": "e7", "source": "left", "target": "join", "branch": "false"},
            {"id": "e8", "source": "right", "target": "join", "branch": "true"},
            {"id": "e9", "source": "right", "target": "join", "branch": "false"},
            {"id": "e10", "source": "join", "target": "loop"},
            {"id": "e11", "source": "loop", "target": "body", "branch": "continue"},
            {"id": "e12", "source": "loop", "target": "accepted", "branch": "exit"},
            {"id": "e13", "source": "body", "target": "loop", "branch": "true"},
            {"id": "e14", "source": "body", "target": "loop", "branch": "false"},
        ],
        "limits": {
            "max_steps": 20,
            "max_duration_seconds": 300,
            "max_tokens": 10_000,
            "max_cost_minor": 1_000,
        },
    }


def _linear_definition(*, policy_node: str | None = None) -> dict[str, Any]:
    nodes = [
        {"id": "start", "type": "input", "name": "Input"},
        {"id": "finish", "type": "output", "name": "Output"},
    ]
    if policy_node is not None:
        next(node for node in nodes if node["id"] == policy_node)["policy_bundle_id"] = str(
            IDS["policy"]
        )
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": nodes,
        "edges": [{"id": "e1", "source": "start", "target": "finish"}],
        "limits": {
            "max_steps": 10,
            "max_duration_seconds": 300,
            "max_tokens": 1_000,
            "max_cost_minor": 100,
        },
    }


def _subworkflow_definition(deployment_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {
                "id": "start",
                "type": "input",
                "name": "Input",
                "policy_bundle_id": str(IDS["policy"]),
            },
            {
                "id": "child",
                "type": "subworkflow",
                "name": "Referenced child",
                "config": {
                    "deployment_id": deployment_id,
                    "result_state_key": "child_result",
                },
                "timeout_seconds": 120,
            },
            {"id": "finish", "type": "output", "name": "Output"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "child"},
            {"id": "e2", "source": "child", "target": "finish"},
        ],
        "limits": {
            "max_steps": 10,
            "max_duration_seconds": 300,
            "max_tokens": 1_000,
            "max_cost_minor": 100,
        },
    }


def _tool_definition(*, timeout_seconds: int = 120) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {
                "id": "send_invoice",
                "type": "tool",
                "name": "Governed invoice delivery",
                "policy_bundle_id": str(IDS["policy"]),
                "config": {
                    "tool_id": str(IDS["tool"]),
                    "arguments_state_key": "invoice",
                    "result_state_key": "delivery",
                },
                "timeout_seconds": timeout_seconds,
            },
            {"id": "finish", "type": "output", "name": "Output"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "send_invoice"},
            {"id": "e2", "source": "send_invoice", "target": "finish"},
        ],
        "limits": {
            "max_steps": 10,
            "max_duration_seconds": 300,
            "max_tokens": 1_000,
            "max_cost_minor": 100,
        },
    }


def _agent_definition(*, timeout_seconds: int = 120) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {
                "id": "generate",
                "type": "agent",
                "name": "Governed model generation",
                "model_route_id": str(IDS["model_route"]),
                "policy_bundle_id": str(IDS["policy"]),
                "config": {
                    "input_state_key": "model_input",
                    "result_state_key": "model_output",
                    "max_output_tokens": 128,
                },
                "timeout_seconds": timeout_seconds,
            },
            {"id": "finish", "type": "output", "name": "Output"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "generate"},
            {"id": "e2", "source": "generate", "target": "finish"},
        ],
        "limits": {
            "max_steps": 10,
            "max_duration_seconds": 300,
            "max_tokens": 1_000,
            "max_cost_minor": 100,
        },
    }


def _worker(database_urls: dict[str, str]) -> WorkflowEngineWorker:
    settings = WorkerSettings(worker_database_url=database_urls["worker"])  # type: ignore[call-arg]
    return WorkflowEngineWorker(
        create_engine(database_urls["worker"], pool_pre_ping=True),
        settings,
        f"runsigil-workflow-test-{secrets.token_hex(4)}",
    )


def _drain(worker: WorkflowEngineWorker, maximum: int = 100) -> int:
    processed = 0
    while processed < maximum and worker.process_once():
        processed += 1
    return processed


def test_durable_workflow_checkpoint_fork_and_evaluation(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    expected_path = [
        "start",
        "route",
        "fan",
        "left",
        "right",
        "join",
        "loop",
        "body",
        "loop",
        "body",
        "loop",
        "accepted",
    ]
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"durable-{suffix}",
                "name": "Durable bounded workflow",
                "description": "Milestone 3 deterministic execution proof",
                "definition": _workflow_definition(),
            },
        )
        assert created.status_code == 201, created.text
        workflow = created.json()
        version_id = workflow["latest_version"]["id"]
        assert workflow["latest_version"]["validation"]["executable"] is True

        deployed = client.post(
            f"/v1/workflow-versions/{version_id}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert deployed.status_code == 201, deployed.text
        deployment_id = deployed.json()["id"]

        started = client.post(
            f"/v1/workflow-deployments/{deployment_id}/runs",
            headers=api_headers,
            json={
                "input": {"approved": True, "case_id": "case-redacted-from-metadata"},
                "idempotency_key": f"workflow-run-{suffix}",
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        assert started.json()["run_kind"] == "workflow"
        assert "case-redacted-from-metadata" not in started.text

    worker = _worker(database_urls)
    assert _drain(worker) == len(expected_path)

    with TestClient(app) as client:
        completed = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert completed.status_code == 200, completed.text
        run = completed.json()
        assert run["status"] == "completed"
        assert run["workflow"]["path"] == expected_path
        assert run["workflow"]["step_count"] == len(expected_path)
        assert len(run["workflow"]["checkpoints"]) == len(expected_path) + 1
        assert len(run["workflow"]["attempts"]) == len(expected_path)
        evidence = client.get(f"/v1/runs/{run_id}/evidence", headers=api_headers)
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["manifest"]["workflow"]["path_digest"].startswith("sha256:")
        assert "case-redacted-from-metadata" not in evidence.text

        checkpoint = run["workflow"]["checkpoints"][2]
        forked = client.post(
            f"/v1/workflow-runs/{run_id}/forks",
            headers=api_headers,
            json={
                "checkpoint_id": checkpoint["id"],
                "idempotency_key": f"workflow-fork-{suffix}",
            },
        )
        assert forked.status_code == 202, forked.text
        fork_run_id = forked.json()["id"]
        assert forked.json()["workflow"]["forked_from_checkpoint_id"] == checkpoint["id"]

    assert _drain(worker) == len(expected_path) - len(checkpoint["path"])
    with TestClient(app) as client:
        fork_detail = client.get(f"/v1/runs/{fork_run_id}", headers=api_headers)
        assert fork_detail.status_code == 200
        assert fork_detail.json()["status"] == "completed"
        assert fork_detail.json()["workflow"]["path"] == expected_path

        dataset_response = client.post(
            "/v1/evaluation-datasets",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"dataset-{suffix}",
                "name": "Workflow regression dataset",
                "description": "Encrypted deterministic scenario",
                "scenarios": [
                    {
                        "key": "approved-case",
                        "name": "Approved case follows bounded path",
                        "input": {"approved": True, "case_id": "evaluation-secret-value"},
                        "expected_output": {
                            "approved": True,
                            "case_id": "evaluation-secret-value",
                        },
                        "expected_path": expected_path,
                        "metadata": {"data_classification": "confidential"},
                    }
                ],
            },
        )
        assert dataset_response.status_code == 201, dataset_response.text
        dataset = dataset_response.json()
        assert "evaluation-secret-value" not in dataset_response.text
        evaluation_response = client.post(
            "/v1/evaluations",
            headers=api_headers,
            json={
                "deployment_id": deployment_id,
                "dataset_version_id": dataset["version_id"],
                "idempotency_key": f"evaluation-{suffix}",
                "minimum_score_milli": 1000,
                "maximum_regression_milli": 0,
            },
        )
        assert evaluation_response.status_code == 202, evaluation_response.text
        evaluation_id = evaluation_response.json()["id"]

    assert _drain(worker) == len(expected_path)
    with TestClient(app) as client:
        evaluation_detail = client.get(f"/v1/evaluations/{evaluation_id}", headers=api_headers)
        assert evaluation_detail.status_code == 200, evaluation_detail.text
        evaluation = evaluation_detail.json()
        assert evaluation["status"] == "completed"
        assert evaluation["score_milli"] == 1000
        assert evaluation["release_gate_status"] == "passed"
        assert evaluation["regression_status"] == "not_configured"
        assert evaluation["results"][0]["task_outcome"] == "passed"
        assert evaluation["results"][0]["trajectory_outcome"] == "passed"
        assert "evaluation-secret-value" not in evaluation_detail.text
        evaluation_evidence = client.get(
            f"/v1/runs/{evaluation['results'][0]['run_id']}/evidence",
            headers=api_headers,
        )
        assert evaluation_evidence.status_code == 200, evaluation_evidence.text
        assert (
            evaluation_evidence.json()["manifest"]["evaluation"]["evaluation_id"] == evaluation_id
        )

        comparison_response = client.post(
            "/v1/evaluations",
            headers=api_headers,
            json={
                "deployment_id": deployment_id,
                "dataset_version_id": dataset["version_id"],
                "baseline_evaluation_id": evaluation_id,
                "idempotency_key": f"evaluation-comparison-{suffix}",
                "minimum_score_milli": 1000,
                "maximum_regression_milli": 0,
            },
        )
        assert comparison_response.status_code == 202, comparison_response.text
        comparison_id = comparison_response.json()["id"]

    assert _drain(worker) == len(expected_path)
    with TestClient(app) as client:
        comparison_detail = client.get(f"/v1/evaluations/{comparison_id}", headers=api_headers)
        assert comparison_detail.status_code == 200, comparison_detail.text
        comparison = comparison_detail.json()
        assert comparison["status"] == "completed"
        assert comparison["score_milli"] == 1000
        assert comparison["baseline_score_milli"] == 1000
        assert comparison["score_delta_milli"] == 0
        assert comparison["regression_status"] == "passed"
        assert comparison["release_gate_status"] == "passed"

        result_id = evaluation["results"][0]["id"]
        annotation_response = client.post(
            f"/v1/evaluation-results/{result_id}/annotations",
            headers=api_headers,
            json={
                "idempotency_key": f"annotation-{suffix}",
                "label": "passed",
                "score_milli": 1000,
                "reason_codes": ["trajectory_verified", "safe_outcome"],
            },
        )
        assert annotation_response.status_code == 201, annotation_response.text
        annotation = annotation_response.json()
        assert annotation["reviewer_id"] == str(IDS["user"])
        repeated_annotation = client.post(
            f"/v1/evaluation-results/{result_id}/annotations",
            headers=api_headers,
            json={
                "idempotency_key": f"annotation-{suffix}",
                "label": "passed",
                "score_milli": 1000,
                "reason_codes": ["trajectory_verified", "safe_outcome"],
            },
        )
        assert repeated_annotation.status_code == 201
        assert repeated_annotation.json()["id"] == annotation["id"]
        annotated_evaluation = client.get(f"/v1/evaluations/{evaluation_id}", headers=api_headers)
        assert annotated_evaluation.status_code == 200
        assert annotated_evaluation.json()["results"][0]["annotations"][0]["id"] == annotation["id"]

    owner = create_engine(database_urls["owner"])
    with Session(owner) as session:
        scenario = session.scalar(
            select(EvaluationScenario).where(
                EvaluationScenario.dataset_version_id == UUID(dataset["version_id"])
            )
        )
        assert scenario is not None
        assert scenario.encrypted_payload.startswith("rsenc1:")
        assert "evaluation-secret-value" not in json.dumps(scenario.metadata_json)
        stored_annotation = session.get(EvaluationAnnotation, UUID(annotation["id"]))
        assert stored_annotation is not None
        assert stored_annotation.reason_codes_json == ["trajectory_verified", "safe_outcome"]
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                text("UPDATE evaluation_annotations SET label = 'failed' WHERE id = :id"),
                {"id": stored_annotation.id},
            )


def test_incomplete_agent_node_cannot_be_deployed(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    definition = {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {"id": "agent", "type": "agent", "name": "Agent"},
            {"id": "done", "type": "output", "name": "Output"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "agent"},
            {"id": "e2", "source": "agent", "target": "done"},
        ],
        "limits": {
            "max_steps": 10,
            "max_duration_seconds": 60,
            "max_tokens": 1_000,
            "max_cost_minor": 100,
        },
    }
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"blocked-agent-{suffix}",
                "name": "Blocked unsupported workflow",
                "definition": definition,
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["latest_version"]["validation"]["executable"] is False
        version_id = created.json()["latest_version"]["id"]
        deployment = client.post(
            f"/v1/workflow-versions/{version_id}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert deployment.status_code == 422
        assert deployment.json()["code"] == "RUNSIGIL_VALIDATION_FAILED"
        assert deployment.json()["details"]["issues"][0]["code"] == "agent_model_route_missing"


def test_expired_workflow_claim_is_recovered_without_duplicate_step(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    definition = {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {"id": "done", "type": "output", "name": "Output"},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "done"}],
        "limits": {
            "max_steps": 5,
            "max_duration_seconds": 60,
            "max_tokens": 100,
            "max_cost_minor": 10,
        },
    }
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"claim-recovery-{suffix}",
                "name": "Claim recovery workflow",
                "definition": definition,
            },
        )
        assert created.status_code == 201, created.text
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert deployed.status_code == 201, deployed.text
        started = client.post(
            f"/v1/workflow-deployments/{deployed.json()['id']}/runs",
            headers=api_headers,
            json={"input": {"safe": True}, "idempotency_key": f"claim-{suffix}"},
        )
        assert started.status_code == 202, started.text
        run_id = UUID(started.json()["id"])

    worker = _worker(database_urls)
    stale_claim = worker.claim_ready()
    assert stale_claim is not None
    owner = create_engine(database_urls["owner"])
    with owner.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflow_executions "
                "SET lease_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE id = :execution_id"
            ),
            {"execution_id": stale_claim.execution_id},
        )
    recovered_claim = worker.claim_ready()
    assert recovered_claim is not None
    assert recovered_claim.execution_id == stale_claim.execution_id
    assert recovered_claim.claim_token != stale_claim.claim_token

    worker.advance(stale_claim)
    with Session(owner) as session:
        execution = session.scalar(
            select(WorkflowExecution).where(WorkflowExecution.run_id == run_id)
        )
        assert execution is not None
        assert execution.step_count == 0

    worker.advance(recovered_claim)
    assert _drain(worker) == 1
    with Session(owner) as session:
        execution = session.scalar(
            select(WorkflowExecution).where(WorkflowExecution.run_id == run_id)
        )
        assert execution is not None
        assert execution.status == "completed"
        assert execution.step_count == 2
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == execution.id,
                OutboxEvent.deduplication_key.endswith(":1"),
            )
        )
        assert event is not None
        assert event.attempts == 2


def test_workflow_step_limit_fails_closed_with_evidence(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    definition = {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {"id": "done", "type": "output", "name": "Output"},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "done"}],
        "limits": {
            "max_steps": 1,
            "max_duration_seconds": 60,
            "max_tokens": 100,
            "max_cost_minor": 10,
        },
    }
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"step-limit-{suffix}",
                "name": "Fail-closed step limit workflow",
                "definition": definition,
            },
        )
        assert created.status_code == 201, created.text
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert deployed.status_code == 201, deployed.text
        started = client.post(
            f"/v1/workflow-deployments/{deployed.json()['id']}/runs",
            headers=api_headers,
            json={"input": {"safe": True}, "idempotency_key": f"step-limit-{suffix}"},
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]

    worker = _worker(database_urls)
    assert _drain(worker) == 2
    with TestClient(app) as client:
        failed = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert failed.status_code == 200, failed.text
        detail = failed.json()
        assert detail["status"] == "failed"
        assert detail["error_code"] == "workflow_step_limit_exceeded"
        assert detail["workflow"]["path"] == ["start"]
        evidence = client.get(f"/v1/runs/{run_id}/evidence", headers=api_headers)
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["manifest"]["run"]["status"] == "failed"


def test_durable_timer_approval_information_and_event_waits(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    definition = {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {
                "id": "timer",
                "type": "timer",
                "name": "Durable delay",
                "timeout_seconds": 2,
                "config": {"delay_seconds": 1},
            },
            {
                "id": "approval",
                "type": "approval",
                "name": "Release approval",
                "timeout_seconds": 10,
                "config": {"risk": "high", "reason_code": "release_review"},
            },
            {
                "id": "information",
                "type": "request_information",
                "name": "Release information",
                "timeout_seconds": 10,
                "config": {"state_key": "review", "reason_code": "review_details"},
            },
            {
                "id": "event",
                "type": "event",
                "name": "Release event",
                "timeout_seconds": 10,
                "config": {"event_key": "release_ready", "state_key": "release"},
            },
            {"id": "denied", "type": "output", "name": "Denied"},
            {"id": "done", "type": "output", "name": "Done"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "timer"},
            {"id": "e2", "source": "timer", "target": "approval"},
            {
                "id": "e3",
                "source": "approval",
                "target": "information",
                "branch": "approved",
            },
            {
                "id": "e4",
                "source": "approval",
                "target": "denied",
                "branch": "denied",
            },
            {"id": "e5", "source": "information", "target": "event"},
            {"id": "e6", "source": "event", "target": "done"},
        ],
        "limits": {
            "max_steps": 10,
            "max_duration_seconds": 30,
            "max_tokens": 100,
            "max_cost_minor": 10,
        },
    }
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"durable-waits-{suffix}",
                "name": "Durable waits workflow",
                "definition": definition,
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["latest_version"]["validation"]["executable"] is True
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert deployed.status_code == 201, deployed.text
        started = client.post(
            f"/v1/workflow-deployments/{deployed.json()['id']}/runs",
            headers=api_headers,
            json={"input": {"safe": True}, "idempotency_key": f"wait-run-{suffix}"},
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]

    worker = _worker(database_urls)
    assert _drain(worker) == 2
    time.sleep(1.1)
    assert _drain(worker) == 2

    with TestClient(app) as client:
        waiting = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert waiting.status_code == 200, waiting.text
        detail = waiting.json()
        assert detail["status"] == "waiting_for_approval"
        approval_wait = detail["workflow"]["waits"][-1]
        assert approval_wait["wait_type"] == "approval"
        assert approval_wait["status"] == "pending"
        mismatched = client.post(
            f"/v1/workflow-waits/{approval_wait['id']}/decision",
            headers=api_headers,
            json={"content_digest": "sha256:" + "0" * 64, "decision": "approved"},
        )
        assert mismatched.status_code == 409
        approved = client.post(
            f"/v1/workflow-waits/{approval_wait['id']}/decision",
            headers=api_headers,
            json={"content_digest": approval_wait["content_digest"], "decision": "approved"},
        )
        assert approved.status_code == 200, approved.text
        replayed = client.post(
            f"/v1/workflow-waits/{approval_wait['id']}/decision",
            headers=api_headers,
            json={"content_digest": approval_wait["content_digest"], "decision": "approved"},
        )
        assert replayed.status_code == 409

    assert _drain(worker) == 2
    with TestClient(app) as client:
        information_detail = client.get(f"/v1/runs/{run_id}", headers=api_headers).json()
        information_wait = information_detail["workflow"]["waits"][-1]
        assert information_wait["wait_type"] == "request_information"
        information = client.post(
            f"/v1/workflow-waits/{information_wait['id']}/information",
            headers=api_headers,
            json={
                "content_digest": information_wait["content_digest"],
                "information": {"ticket": "sensitive-review-ticket"},
            },
        )
        assert information.status_code == 200, information.text
        assert "sensitive-review-ticket" not in information.text

    assert _drain(worker) == 2
    with TestClient(app) as client:
        event_detail = client.get(f"/v1/runs/{run_id}", headers=api_headers).json()
        event_wait = event_detail["workflow"]["waits"][-1]
        assert event_wait["wait_type"] == "event"
        wrong_event = client.post(
            f"/v1/workflow-waits/{event_wait['id']}/event",
            headers=api_headers,
            json={
                "content_digest": event_wait["content_digest"],
                "event_key": "wrong_event",
                "payload": {"release": "sensitive-release-reference"},
            },
        )
        assert wrong_event.status_code == 409
        event = client.post(
            f"/v1/workflow-waits/{event_wait['id']}/event",
            headers=api_headers,
            json={
                "content_digest": event_wait["content_digest"],
                "event_key": "release_ready",
                "payload": {"release": "sensitive-release-reference"},
            },
        )
        assert event.status_code == 200, event.text
        assert "sensitive-release-reference" not in event.text

    assert _drain(worker) == 2
    with TestClient(app) as client:
        completed = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert completed.status_code == 200, completed.text
        detail = completed.json()
        assert detail["status"] == "completed"
        assert detail["workflow"]["path"] == [
            "start",
            "timer",
            "approval",
            "information",
            "event",
            "done",
        ]
        assert [wait["status"] for wait in detail["workflow"]["waits"]] == [
            "resolved",
            "resolved",
            "resolved",
            "resolved",
        ]
        assert "sensitive-review-ticket" not in completed.text
        assert "sensitive-release-reference" not in completed.text
        evidence = client.get(f"/v1/runs/{run_id}/evidence", headers=api_headers)
        assert evidence.status_code == 200, evidence.text
        assert len(evidence.json()["manifest"]["waits"]) == 4

    owner = create_engine(database_urls["owner"])
    with Session(owner) as session:
        stored_waits = list(
            session.scalars(
                select(WorkflowWait)
                .where(WorkflowWait.run_id == UUID(run_id))
                .order_by(WorkflowWait.sequence)
            )
        )
        assert stored_waits[2].encrypted_response is not None
        assert stored_waits[2].encrypted_response.startswith("rsenc1:")
        assert "sensitive-review-ticket" not in json.dumps(stored_waits[2].request_metadata_json)
        with pytest.raises(DBAPIError, match="single use"):
            session.execute(
                text("UPDATE workflow_waits SET resolution = 'denied' WHERE id = :id"),
                {"id": UUID(approval_wait["id"])},
            )


def test_workflow_wait_timeout_fails_closed(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    definition = {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {
                "id": "approval",
                "type": "approval",
                "name": "Expiring approval",
                "timeout_seconds": 1,
                "config": {"risk": "critical", "reason_code": "timeout_proof"},
            },
            {"id": "denied", "type": "output", "name": "Denied"},
            {"id": "done", "type": "output", "name": "Done"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "approval"},
            {"id": "e2", "source": "approval", "target": "done", "branch": "approved"},
            {"id": "e3", "source": "approval", "target": "denied", "branch": "denied"},
        ],
        "limits": {
            "max_steps": 5,
            "max_duration_seconds": 10,
            "max_tokens": 100,
            "max_cost_minor": 10,
        },
    }
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"wait-timeout-{suffix}",
                "name": "Wait timeout workflow",
                "definition": definition,
            },
        )
        assert created.status_code == 201, created.text
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        started = client.post(
            f"/v1/workflow-deployments/{deployed.json()['id']}/runs",
            headers=api_headers,
            json={"input": {"safe": True}, "idempotency_key": f"timeout-{suffix}"},
        )
        run_id = started.json()["id"]

    worker = _worker(database_urls)
    assert _drain(worker) == 2
    time.sleep(1.1)
    assert _drain(worker) == 1
    with TestClient(app) as client:
        failed = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert failed.status_code == 200, failed.text
        detail = failed.json()
        assert detail["status"] == "failed"
        assert detail["error_code"] == "workflow_wait_expired"
        assert detail["workflow"]["waits"][0]["status"] == "expired"
        evidence = client.get(f"/v1/runs/{run_id}/evidence", headers=api_headers)
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["manifest"]["waits"][0]["resolution"] == "expired"


def test_referenced_subworkflow_policy_replay_and_safety_evaluation(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    with TestClient(app) as client:
        child = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"child-{suffix}",
                "name": "Referenced deterministic child",
                "definition": _linear_definition(),
            },
        )
        assert child.status_code == 201, child.text
        child_deployment = client.post(
            f"/v1/workflow-versions/{child.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert child_deployment.status_code == 201, child_deployment.text
        parent = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"parent-{suffix}",
                "name": "Policy-bound parent",
                "definition": _subworkflow_definition(child_deployment.json()["id"]),
            },
        )
        assert parent.status_code == 201, parent.text
        parent_deployment = client.post(
            f"/v1/workflow-versions/{parent.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert parent_deployment.status_code == 201, parent_deployment.text
        deployment_id = parent_deployment.json()["id"]
        started = client.post(
            f"/v1/workflow-deployments/{deployment_id}/runs",
            headers=api_headers,
            json={
                "input": {"case": "confidential-live-value"},
                "idempotency_key": f"subworkflow-{suffix}",
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        assert "confidential-live-value" not in started.text

    worker = _worker(database_urls)
    assert _drain(worker) >= 6
    with TestClient(app) as client:
        completed = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert completed.status_code == 200, completed.text
        workflow = completed.json()["workflow"]
        assert completed.json()["status"] == "completed"
        assert workflow["path"] == ["start", "child", "finish"]
        assert workflow["subworkflows"][0]["status"] == "completed"
        assert workflow["subworkflows"][0]["result_state_digest"].startswith("sha256:")
        subworkflow_call_id = workflow["subworkflows"][0]["id"]
        assert workflow["policy_decisions"][0]["node_id"] == "start"
        assert workflow["policy_decisions"][0]["effect"] == "allow"
        policy_decision_id = workflow["policy_decisions"][0]["id"]
        checkpoint_id = workflow["checkpoints"][0]["id"]

        replayed = client.post(
            f"/v1/workflow-runs/{run_id}/replays",
            headers=api_headers,
            json={
                "checkpoint_id": checkpoint_id,
                "idempotency_key": f"replay-{suffix}",
            },
        )
        assert replayed.status_code == 202, replayed.text
        replay_run_id = replayed.json()["id"]

    assert _drain(worker) >= 6
    with TestClient(app) as client:
        replay_detail = client.get(f"/v1/runs/{replay_run_id}", headers=api_headers)
        assert replay_detail.status_code == 200, replay_detail.text
        replay = replay_detail.json()["workflow"]["replay"]
        assert replay["status"] == "matched"
        assert replay["replay_state_digest"] == replay["source_state_digest"]
        assert replay["replay_path_digest"] == replay["source_path_digest"]
        replay_id = replay["id"]

        dataset = client.post(
            "/v1/evaluation-datasets",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"policy-safety-{suffix}",
                "name": "Policy and safety assertions",
                "scenarios": [
                    {
                        "key": "safe-nested-run",
                        "name": "Safe nested execution",
                        "input": {"case": "evaluation-secret"},
                        "expected_output": {
                            "case": "evaluation-secret",
                            "child_result": {"case": "evaluation-secret"},
                        },
                        "expected_path": ["start", "child", "finish"],
                        "assertions": {
                            "required_policy_nodes": ["start"],
                            "forbidden_nodes": ["unsafe_tool"],
                            "maximum_steps": 3,
                        },
                    }
                ],
            },
        )
        assert dataset.status_code == 201, dataset.text
        evaluation = client.post(
            "/v1/evaluations",
            headers=api_headers,
            json={
                "deployment_id": deployment_id,
                "dataset_version_id": dataset.json()["version_id"],
                "idempotency_key": f"policy-safety-evaluation-{suffix}",
                "minimum_score_milli": 1_000,
            },
        )
        assert evaluation.status_code == 202, evaluation.text
        evaluation_id = evaluation.json()["id"]

    assert _drain(worker) >= 6
    with TestClient(app) as client:
        evaluation = client.get(f"/v1/evaluations/{evaluation_id}", headers=api_headers)
        assert evaluation.status_code == 200, evaluation.text
        result = evaluation.json()["results"][0]
        assert result["policy_outcome"] == "passed"
        assert result["safety_outcome"] == "passed"
        assert result["score_milli"] == 1_000
        assert [grader["grader"] for grader in result["graders"]] == [
            "task_outcome",
            "trajectory",
            "deterministic_environment",
            "policy",
            "safety",
        ]

    worker_engine = create_engine(database_urls["worker"])
    with Session(worker_engine) as session:
        assert session.get(WorkflowSubworkflowCall, UUID(subworkflow_call_id)) is not None
        timeout_event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.deduplication_key == f"subworkflow.call:{subworkflow_call_id}:timeout"
            )
        )
        assert timeout_event is not None and timeout_event.processed_at is not None
        with pytest.raises(DBAPIError, match="single use"):
            session.execute(
                text("UPDATE workflow_subworkflow_calls SET status = 'failed' WHERE id = :id"),
                {"id": UUID(subworkflow_call_id)},
            )
    owner_engine = create_engine(database_urls["owner"])
    with Session(owner_engine) as session:
        assert session.get(WorkflowPolicyDecision, UUID(policy_decision_id)) is not None
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                text("UPDATE workflow_policy_decisions SET effect = 'deny' WHERE id = :id"),
                {"id": UUID(policy_decision_id)},
            )
    with Session(worker_engine) as session:
        assert session.get(WorkflowReplay, UUID(replay_id)) is not None
        with pytest.raises(DBAPIError, match="single use"):
            session.execute(
                text("UPDATE workflow_replays SET status = 'failed' WHERE id = :id"),
                {"id": UUID(replay_id)},
            )


def test_idle_workflow_cancellation_is_finalized_with_signed_evidence(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    definition = {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {
                "id": "pause",
                "type": "timer",
                "name": "Cancelable timer",
                "config": {"delay_seconds": 300},
                "timeout_seconds": 600,
            },
            {"id": "finish", "type": "output", "name": "Output"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "pause"},
            {"id": "e2", "source": "pause", "target": "finish"},
        ],
        "limits": {
            "max_steps": 5,
            "max_duration_seconds": 900,
            "max_tokens": 100,
            "max_cost_minor": 10,
        },
    }
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"cancel-{suffix}",
                "name": "Cancelable deterministic workflow",
                "definition": definition,
            },
        )
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        started = client.post(
            f"/v1/workflow-deployments/{deployed.json()['id']}/runs",
            headers=api_headers,
            json={"input": {"safe": True}, "idempotency_key": f"cancel-{suffix}"},
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]

    worker = _worker(database_urls)
    assert _drain(worker) == 2
    with TestClient(app) as client:
        cancelled = client.post(f"/v1/runs/{run_id}/cancel", headers=api_headers)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["workflow"]["waits"][0]["status"] == "cancelled"
        assert cancelled.json()["evidence_status"] == "pending"

    assert worker.process_once() is True
    with TestClient(app) as client:
        detail = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "cancelled"
        assert detail.json()["error_code"] == "workflow_cancelled"
        assert detail.json()["evidence_status"] == "local_only"
        evidence = client.get(f"/v1/runs/{run_id}/evidence", headers=api_headers)
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["manifest"]["run"]["status"] == "cancelled"
        assert evidence.json()["manifest"]["waits"][0]["resolution"] == "cancelled"


def test_workflow_node_policy_unavailability_fails_closed(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"policy-fail-closed-{suffix}",
                "name": "Policy fail-closed workflow",
                "definition": _linear_definition(policy_node="start"),
            },
        )
        assert created.status_code == 201, created.text
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert deployed.status_code == 201, deployed.text

    owner_engine = create_engine(database_urls["owner"])
    try:
        with owner_engine.begin() as connection:
            connection.execute(
                text("UPDATE policy_bundles SET status = 'inactive' WHERE id = :id"),
                {"id": IDS["policy"]},
            )
        with TestClient(app) as client:
            started = client.post(
                f"/v1/workflow-deployments/{deployed.json()['id']}/runs",
                headers=api_headers,
                json={"input": {"safe": True}, "idempotency_key": f"policy-{suffix}"},
            )
            assert started.status_code == 202, started.text
            run_id = started.json()["id"]
        worker = _worker(database_urls)
        assert worker.process_once() is True
        with TestClient(app) as client:
            failed = client.get(f"/v1/runs/{run_id}", headers=api_headers)
            assert failed.status_code == 200, failed.text
            assert failed.json()["status"] == "failed"
            assert failed.json()["error_code"] == "RUNSIGIL_POLICY_UNAVAILABLE"
            assert failed.json()["workflow"]["policy_decisions"] == []
            assert failed.json()["evidence_status"] == "local_only"
    finally:
        with owner_engine.begin() as connection:
            connection.execute(
                text("UPDATE policy_bundles SET status = 'active' WHERE id = :id"),
                {"id": IDS["policy"]},
            )


def test_parent_cancellation_propagates_to_waiting_subworkflow(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    child_definition = {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {
                "id": "pause",
                "type": "timer",
                "name": "Long child wait",
                "config": {"delay_seconds": 300},
                "timeout_seconds": 600,
            },
            {"id": "finish", "type": "output", "name": "Output"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "pause"},
            {"id": "e2", "source": "pause", "target": "finish"},
        ],
        "limits": {
            "max_steps": 5,
            "max_duration_seconds": 900,
            "max_tokens": 100,
            "max_cost_minor": 10,
        },
    }
    with TestClient(app) as client:
        child = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"cancel-child-{suffix}",
                "name": "Cancelable child",
                "definition": child_definition,
            },
        )
        child_deployment = client.post(
            f"/v1/workflow-versions/{child.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        parent = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"cancel-parent-{suffix}",
                "name": "Cancelable parent",
                "definition": _subworkflow_definition(child_deployment.json()["id"]),
            },
        )
        parent_deployment = client.post(
            f"/v1/workflow-versions/{parent.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        started = client.post(
            f"/v1/workflow-deployments/{parent_deployment.json()['id']}/runs",
            headers=api_headers,
            json={"input": {"safe": True}, "idempotency_key": f"nested-cancel-{suffix}"},
        )
        assert started.status_code == 202, started.text
        parent_run_id = started.json()["id"]

    worker = _worker(database_urls)
    assert _drain(worker) == 4
    with TestClient(app) as client:
        waiting_parent = client.get(f"/v1/runs/{parent_run_id}", headers=api_headers)
        call = waiting_parent.json()["workflow"]["subworkflows"][0]
        child_run_id = call["child_run_id"]
        assert call["status"] == "pending"
        cancelled = client.post(f"/v1/runs/{parent_run_id}/cancel", headers=api_headers)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["workflow"]["subworkflows"][0]["status"] == "cancelled"

    assert worker.process_once() is True  # parent evidence finalization
    assert worker.process_once() is True  # child observes terminal parent call
    with TestClient(app) as client:
        child_detail = client.get(f"/v1/runs/{child_run_id}", headers=api_headers)
        assert child_detail.status_code == 200, child_detail.text
        assert child_detail.json()["status"] == "cancelled"
        assert child_detail.json()["error_code"] == "parent_subworkflow_cancelled"
        assert child_detail.json()["workflow"]["waits"][0]["status"] == "cancelled"
        assert child_detail.json()["evidence_status"] == "local_only"


def test_governed_tool_node_uses_child_action_approval_and_signed_evidence(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    private_recipient = f"phase-five-{suffix}@example.test"
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"governed-tool-{suffix}",
                "name": "Governed tool workflow",
                "definition": _tool_definition(),
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["latest_version"]["validation"]["executable"] is True
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert deployed.status_code == 201, deployed.text
        deployment_id = deployed.json()["id"]
        started = client.post(
            f"/v1/workflow-deployments/{deployment_id}/runs",
            headers=api_headers,
            json={
                "input": {
                    "invoice": {
                        "recipient": private_recipient,
                        "amount_cents": 2_400,
                        "description": "Phase five governed delivery",
                        "simulate_outcome": "committed",
                    }
                },
                "idempotency_key": f"governed-tool-run-{suffix}",
            },
        )
        assert started.status_code == 202, started.text
        parent_run_id = started.json()["id"]
        assert private_recipient not in started.text

    workflow_worker = _worker(database_urls)
    assert _drain(workflow_worker) == 2
    with TestClient(app) as client:
        waiting = client.get(f"/v1/runs/{parent_run_id}", headers=api_headers)
        assert waiting.status_code == 200, waiting.text
        assert waiting.json()["status"] == "waiting"
        call = waiting.json()["workflow"]["tool_calls"][0]
        assert call["status"] == "pending_approval"
        assert call["arguments_digest"].startswith("sha256:")
        assert private_recipient not in waiting.text
        child_run_id = call["child_run_id"]
        action_id = call["action_id"]
        child = client.get(f"/v1/runs/{child_run_id}", headers=api_headers)
        assert child.status_code == 200, child.text
        approval = child.json()["approval"]
        assert approval["status"] == "pending"
        assert private_recipient not in child.text
        approved = client.post(
            f"/v1/approvals/{approval['id']}/decision",
            headers=api_headers,
            json={
                "content_digest": approval["content_digest"],
                "decision": "approve",
                "reason": "Integration proof for exact governed workflow content",
            },
        )
        assert approved.status_code == 200, approved.text
        unsafe_cancel = client.post(
            f"/v1/runs/{parent_run_id}/cancel",
            headers=api_headers,
        )
        assert unsafe_cancel.status_code == 409, unsafe_cancel.text

    action_worker = ActionWorker(engine=create_engine(database_urls["worker"]))
    claim = None
    for _ in range(100):
        candidate = action_worker.claim_ready()
        assert candidate is not None
        if str(candidate.action_id) == action_id:
            claim = candidate
            break
        action_worker.settle(
            candidate,
            ActionExecutionResult(outcome="failed", error_code="test_queue_cleanup"),
        )
    assert claim is not None
    action_worker.settle(
        claim,
        ActionExecutionResult(
            outcome="committed",
            receipt_preview={"status": "accepted", "amount_cents": 2_400},
            provider_reference=f"phase-five-{suffix}",
        ),
    )
    assert _drain(workflow_worker) == 2

    with TestClient(app) as client:
        completed = client.get(f"/v1/runs/{parent_run_id}", headers=api_headers)
        assert completed.status_code == 200, completed.text
        detail = completed.json()
        assert detail["status"] == "completed"
        assert detail["workflow"]["path"] == ["start", "send_invoice", "finish"]
        settled_call = detail["workflow"]["tool_calls"][0]
        assert settled_call["status"] == "completed"
        assert settled_call["result_digest"].startswith("sha256:")
        assert private_recipient not in completed.text
        child_evidence = client.get(
            f"/v1/runs/{child_run_id}/evidence",
            headers=api_headers,
        )
        assert child_evidence.status_code == 200, child_evidence.text
        parent_evidence = client.get(
            f"/v1/runs/{parent_run_id}/evidence",
            headers=api_headers,
        )
        assert parent_evidence.status_code == 200, parent_evidence.text
        evidence_call = parent_evidence.json()["manifest"]["tool_calls"][0]
        assert evidence_call["status"] == "completed"
        assert evidence_call["child_evidence_digest"] == child_evidence.json()["content_digest"]
        assert private_recipient not in parent_evidence.text

        checkpoint_id = detail["workflow"]["checkpoints"][0]["id"]
        forked = client.post(
            f"/v1/workflow-runs/{parent_run_id}/forks",
            headers=api_headers,
            json={
                "checkpoint_id": checkpoint_id,
                "idempotency_key": f"effectful-fork-{suffix}",
            },
        )
        assert forked.status_code == 409, forked.text
        replayed = client.post(
            f"/v1/workflow-runs/{parent_run_id}/replays",
            headers=api_headers,
            json={
                "checkpoint_id": checkpoint_id,
                "idempotency_key": f"effectful-replay-{suffix}",
            },
        )
        assert replayed.status_code == 409, replayed.text
        dataset = client.post(
            "/v1/evaluation-datasets",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"effectful-eval-{suffix}",
                "name": "Effectful evaluation rejection proof",
                "scenarios": [
                    {
                        "key": "must-simulate",
                        "name": "Tool effects require simulation",
                        "input": {"invoice": {"amount_cents": 2_400}},
                        "expected_output": {"blocked": True},
                    }
                ],
            },
        )
        assert dataset.status_code == 201, dataset.text
        evaluation = client.post(
            "/v1/evaluations",
            headers=api_headers,
            json={
                "deployment_id": deployment_id,
                "dataset_version_id": dataset.json()["version_id"],
                "idempotency_key": f"effectful-evaluation-{suffix}",
                "minimum_score_milli": 1_000,
            },
        )
        assert evaluation.status_code == 409, evaluation.text

        cancelable = client.post(
            f"/v1/workflow-deployments/{deployment_id}/runs",
            headers=api_headers,
            json={
                "input": {
                    "invoice": {
                        "recipient": f"cancel-{suffix}@example.test",
                        "amount_cents": 1_200,
                        "description": "Cancel before exact approval",
                    }
                },
                "idempotency_key": f"cancel-tool-run-{suffix}",
            },
        )
        assert cancelable.status_code == 202, cancelable.text
        cancelable_run_id = cancelable.json()["id"]

    assert _drain(workflow_worker) == 2
    with TestClient(app) as client:
        cancelled = client.post(
            f"/v1/runs/{cancelable_run_id}/cancel",
            headers=api_headers,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        cancelled_call = cancelled.json()["workflow"]["tool_calls"][0]
        assert cancelled_call["status"] == "cancelled"
        cancelled_child = client.get(
            f"/v1/runs/{cancelled_call['child_run_id']}",
            headers=api_headers,
        )
        assert cancelled_child.status_code == 200, cancelled_child.text
        assert cancelled_child.json()["status"] == "cancelled"
    assert workflow_worker.process_once() is True

    worker_engine = create_engine(database_urls["worker"])
    with Session(worker_engine) as session:
        stored_call = session.get(WorkflowToolCall, UUID(call["id"]))
        stored_action = session.get(Action, UUID(action_id))
        assert stored_call is not None and stored_action is not None
        assert stored_action.state == "committed"
        timeout_event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.deduplication_key == f"workflow.tool:{stored_call.id}:timeout"
            )
        )
        assert timeout_event is not None and timeout_event.processed_at is not None
        with pytest.raises(DBAPIError, match="single use"):
            session.execute(
                text("UPDATE workflow_tool_calls SET status = 'failed' WHERE id = :id"),
                {"id": stored_call.id},
            )


def test_governed_tool_timeout_cancels_only_before_effect_dispatch(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"tool-timeout-{suffix}",
                "name": "Pre-effect tool timeout",
                "definition": _tool_definition(timeout_seconds=1),
            },
        )
        assert created.status_code == 201, created.text
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert deployed.status_code == 201, deployed.text
        started = client.post(
            f"/v1/workflow-deployments/{deployed.json()['id']}/runs",
            headers=api_headers,
            json={
                "input": {
                    "invoice": {
                        "recipient": f"timeout-{suffix}@example.test",
                        "amount_cents": 800,
                        "description": "Must never dispatch",
                    }
                },
                "idempotency_key": f"tool-timeout-{suffix}",
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]

    workflow_worker = _worker(database_urls)
    assert _drain(workflow_worker) == 2
    time.sleep(1.1)
    assert workflow_worker.process_once() is True

    with TestClient(app) as client:
        failed = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert failed.status_code == 200, failed.text
        detail = failed.json()
        assert detail["status"] == "failed"
        assert detail["error_code"] == "workflow_tool_timed_out"
        call = detail["workflow"]["tool_calls"][0]
        assert call["status"] == "timed_out"
        child = client.get(f"/v1/runs/{call['child_run_id']}", headers=api_headers)
        assert child.status_code == 200, child.text
        assert child.json()["status"] == "cancelled"
        assert child.json()["action"]["state"] == "rejected"
        evidence = client.get(f"/v1/runs/{run_id}/evidence", headers=api_headers)
        assert evidence.status_code == 200, evidence.text
        evidence_call = evidence.json()["manifest"]["tool_calls"][0]
        assert evidence_call["status"] == "timed_out"
        assert evidence_call["child_evidence_digest"] is None

    with Session(create_engine(database_urls["owner"])) as session:
        action_id = UUID(call["action_id"])
        action = session.get(Action, action_id)
        assert action is not None and action.execute_attempts == 0
        action_event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == action_id,
                OutboxEvent.topic == "action.ready",
            )
        )
        assert action_event is None


def test_governed_tool_ambiguous_outcome_uses_dlq_redrive_without_redispatch(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"tool-reconcile-{suffix}",
                "name": "Ambiguous tool reconciliation",
                "definition": _tool_definition(),
            },
        )
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        started = client.post(
            f"/v1/workflow-deployments/{deployed.json()['id']}/runs",
            headers=api_headers,
            json={
                "input": {
                    "invoice": {
                        "recipient": f"reconcile-{suffix}@example.test",
                        "amount_cents": 1_600,
                        "description": "Reconcile receipt only",
                        "simulate_outcome": "ambiguous_after_commit",
                    }
                },
                "idempotency_key": f"tool-reconcile-{suffix}",
            },
        )
        assert started.status_code == 202, started.text
        parent_run_id = started.json()["id"]

    workflow_worker = _worker(database_urls)
    assert _drain(workflow_worker) == 2
    with TestClient(app) as client:
        waiting = client.get(f"/v1/runs/{parent_run_id}", headers=api_headers).json()
        call = waiting["workflow"]["tool_calls"][0]
        action_id = call["action_id"]
        child = client.get(f"/v1/runs/{call['child_run_id']}", headers=api_headers).json()
        approval = child["approval"]
        approved = client.post(
            f"/v1/approvals/{approval['id']}/decision",
            headers=api_headers,
            json={
                "content_digest": approval["content_digest"],
                "decision": "approve",
                "reason": "Exercise reconcile-only recovery",
            },
        )
        assert approved.status_code == 200, approved.text

    settings = WorkerSettings(
        worker_database_url=database_urls["worker"],
        max_reconciliation_attempts=1,
        max_dlq_redrives=1,
        reconciliation_delay_seconds=1,
    )
    action_worker = ActionWorker(
        settings=settings,
        engine=create_engine(database_urls["worker"]),
    )
    claim = None
    for _ in range(100):
        candidate = action_worker.claim_ready()
        assert candidate is not None
        if str(candidate.action_id) == action_id:
            claim = candidate
            break
        action_worker.settle(
            candidate,
            ActionExecutionResult(outcome="failed", error_code="test_queue_cleanup"),
        )
    assert claim is not None and claim.mode == "execute"
    action_worker.settle(
        claim,
        ActionExecutionResult(outcome="ambiguous", error_code="receipt_unknown"),
    )

    with TestClient(app) as client:
        ambiguous_parent = client.get(f"/v1/runs/{parent_run_id}", headers=api_headers).json()
        assert ambiguous_parent["status"] == "waiting"
        assert ambiguous_parent["workflow"]["tool_calls"][0]["status"] == "reconciliation_required"
        assert ambiguous_parent["evidence_status"] == "pending"

    owner_engine = create_engine(database_urls["owner"])

    def make_target_reconciliation_due() -> None:
        with Session(owner_engine) as session, session.begin():
            now = database_now(session)
            actions = list(
                session.scalars(select(Action).where(Action.state == "reconciliation_required"))
            )
            for action in actions:
                action.next_reconcile_at = (
                    now - timedelta(seconds=1)
                    if str(action.id) == action_id
                    else now + timedelta(hours=1)
                )

    make_target_reconciliation_due()
    reconcile_claim = action_worker.claim_reconciliation()
    assert reconcile_claim is not None
    assert str(reconcile_claim.action_id) == action_id
    assert reconcile_claim.mode == "reconcile"
    action_worker.settle(
        reconcile_claim,
        ActionExecutionResult(outcome="ambiguous", error_code="still_unknown"),
    )

    with Session(owner_engine) as session:
        action = session.get(Action, UUID(action_id))
        assert action is not None and action.state == "dead_lettered"
        dead_letter = session.scalar(select(DeadLetter).where(DeadLetter.action_id == action.id))
        assert dead_letter is not None and dead_letter.status == "open"
        dead_letter_id = str(dead_letter.id)
        dead_letter_version = dead_letter.version

    with TestClient(app) as client:
        dead_lettered_parent = client.get(f"/v1/runs/{parent_run_id}", headers=api_headers).json()
        assert dead_lettered_parent["status"] == "waiting"
        assert dead_lettered_parent["workflow"]["tool_calls"][0]["status"] == "dead_lettered"
        assert dead_lettered_parent["evidence_status"] == "pending"
        redriven = client.post(
            f"/v1/dead-letters/{dead_letter_id}/redrive",
            headers=api_headers,
            json={
                "expected_version": dead_letter_version,
                "reason": "Reconcile the exact ambiguous tool effect",
            },
        )
        assert redriven.status_code == 200, redriven.text
        assert redriven.json()["status"] == "redriven"
        redriven_parent = client.get(f"/v1/runs/{parent_run_id}", headers=api_headers).json()
        assert redriven_parent["workflow"]["tool_calls"][0]["status"] == "reconciliation_required"

    make_target_reconciliation_due()
    redrive_claim = action_worker.claim_reconciliation()
    assert redrive_claim is not None
    assert str(redrive_claim.action_id) == action_id
    assert redrive_claim.mode == "reconcile"
    action_worker.settle(
        redrive_claim,
        ActionExecutionResult(
            outcome="committed",
            receipt_preview={"status": "reconciled"},
            provider_reference=f"reconciled-{suffix}",
        ),
    )
    assert _drain(workflow_worker) == 2

    with TestClient(app) as client:
        completed = client.get(f"/v1/runs/{parent_run_id}", headers=api_headers)
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        settled_call = completed.json()["workflow"]["tool_calls"][0]
        assert settled_call["status"] == "completed"
    with Session(owner_engine) as session:
        action = session.get(Action, UUID(action_id))
        assert action is not None
        assert action.execute_attempts == 1
        assert action.reconcile_attempts == 2


def test_effectful_fork_replay_and_evaluation_require_explicit_simulation(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    invoice = {
        "recipient": f"simulation-{suffix}@example.test",
        "amount_cents": 1_250,
        "description": "Deterministic simulation proof",
        "simulate_outcome": "committed",
    }
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"simulation-flow-{suffix}",
                "name": "Explicit simulation workflow",
                "definition": _tool_definition(),
            },
        )
        assert created.status_code == 201, created.text
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert deployed.status_code == 201, deployed.text
        deployment_id = deployed.json()["id"]
        started = client.post(
            f"/v1/workflow-deployments/{deployment_id}/runs",
            headers=api_headers,
            json={
                "input": {"invoice": invoice},
                "idempotency_key": f"simulation-source-{suffix}",
            },
        )
        assert started.status_code == 202, started.text
        source_run_id = started.json()["id"]

    worker = _worker(database_urls)
    assert _drain(worker) == 2
    with TestClient(app) as client:
        source = client.get(f"/v1/runs/{source_run_id}", headers=api_headers).json()
        assert source["status"] == "waiting"
        checkpoint_id = source["workflow"]["checkpoints"][0]["id"]
        rejected = client.post(
            f"/v1/workflow-runs/{source_run_id}/forks",
            headers=api_headers,
            json={
                "checkpoint_id": checkpoint_id,
                "idempotency_key": f"unsafe-simulation-fork-{suffix}",
            },
        )
        assert rejected.status_code == 409, rejected.text
        profile_response = client.post(
            "/v1/workflow-simulation-profiles",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "tool_id": str(IDS["tool"]),
                "name": f"deterministic-provider-{suffix}",
            },
        )
        assert profile_response.status_code == 201, profile_response.text
        profile = profile_response.json()
        forked = client.post(
            f"/v1/workflow-runs/{source_run_id}/forks",
            headers=api_headers,
            json={
                "checkpoint_id": checkpoint_id,
                "simulation_profile_id": profile["id"],
                "idempotency_key": f"simulation-fork-{suffix}",
            },
        )
        assert forked.status_code == 202, forked.text
        fork_run_id = forked.json()["id"]
        assert forked.json()["workflow"]["execution_mode"] == "simulation"

    assert _drain(worker) == 3
    with TestClient(app) as client:
        fork_detail = client.get(f"/v1/runs/{fork_run_id}", headers=api_headers)
        assert fork_detail.status_code == 200, fork_detail.text
        fork_document = fork_detail.json()
        assert fork_document["status"] == "completed"
        assert fork_document["workflow"]["simulation_profile_id"] == profile["id"]
        assert fork_document["workflow"]["tool_calls"] == []
        assert len(fork_document["workflow"]["tool_simulations"]) == 1
        assert invoice["recipient"] not in fork_detail.text
        replayed = client.post(
            f"/v1/workflow-runs/{fork_run_id}/replays",
            headers=api_headers,
            json={
                "checkpoint_id": fork_document["workflow"]["checkpoints"][0]["id"],
                "simulation_profile_id": profile["id"],
                "idempotency_key": f"simulation-replay-{suffix}",
            },
        )
        assert replayed.status_code == 202, replayed.text
        replay_run_id = replayed.json()["id"]

    assert _drain(worker) == 3
    owner_engine = create_engine(database_urls["owner"])
    with Session(owner_engine) as session:
        tool = session.get(Tool, IDS["tool"])
        assert tool is not None
        expected_state = {
            "invoice": invoice,
            "delivery": {
                "outcome": "simulated",
                "arguments_digest": canonical_digest(invoice),
                "tool_digest": canonical_digest(tool_document(tool)),
                "simulation_profile_digest": profile["content_digest"],
                "receipt_preview": {
                    "status": "simulated",
                    "side_effect_performed": False,
                },
            },
        }
    with TestClient(app) as client:
        replay_detail = client.get(f"/v1/runs/{replay_run_id}", headers=api_headers)
        assert replay_detail.status_code == 200, replay_detail.text
        assert replay_detail.json()["workflow"]["replay"]["status"] == "matched"
        dataset = client.post(
            "/v1/evaluation-datasets",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"simulation-dataset-{suffix}",
                "name": "Tool simulation dataset",
                "scenarios": [
                    {
                        "key": "simulated-effect",
                        "name": "No side effect is performed",
                        "input": {"invoice": invoice},
                        "expected_output": expected_state,
                        "expected_path": ["start", "send_invoice", "finish"],
                    }
                ],
            },
        )
        assert dataset.status_code == 201, dataset.text
        evaluation = client.post(
            "/v1/evaluations",
            headers=api_headers,
            json={
                "deployment_id": deployment_id,
                "dataset_version_id": dataset.json()["version_id"],
                "simulation_profile_id": profile["id"],
                "idempotency_key": f"simulation-evaluation-{suffix}",
            },
        )
        assert evaluation.status_code == 202, evaluation.text
        evaluation_id = evaluation.json()["id"]

    assert _drain(worker) == 3
    with TestClient(app) as client:
        evaluated = client.get(f"/v1/evaluations/{evaluation_id}", headers=api_headers)
        assert evaluated.status_code == 200, evaluated.text
        assert evaluated.json()["status"] == "completed"
        assert evaluated.json()["simulation_profile_id"] == profile["id"]
        assert evaluated.json()["release_gate_status"] == "passed"
        evidence = client.get(f"/v1/runs/{fork_run_id}/evidence", headers=api_headers)
        assert evidence.status_code == 200, evidence.text
        manifest = evidence.json()["manifest"]
        assert manifest["workflow"]["execution_mode"] == "simulation"
        assert manifest["tool_simulations"][0]["side_effect_performed"] is False
        cancelled = client.post(f"/v1/runs/{source_run_id}/cancel", headers=api_headers)
        assert cancelled.status_code == 200, cancelled.text
    assert worker.process_once() is True


def test_serial_agent_model_call_is_encrypted_budgeted_authorized_and_resumed(
    database_urls: dict[str, str], api_headers: dict[str, str]
) -> None:
    suffix = secrets.token_hex(6)
    private_instruction = f"model-private-{suffix}"
    with TestClient(app) as client:
        created = client.post(
            "/v1/workflows",
            headers=api_headers,
            json={
                "project_id": str(IDS["project"]),
                "slug": f"agent-model-{suffix}",
                "name": "Durable agent model workflow",
                "definition": _agent_definition(),
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["latest_version"]["validation"]["executable"] is True
        deployed = client.post(
            f"/v1/workflow-versions/{created.json()['latest_version']['id']}/deployments",
            headers=api_headers,
            json={
                "environment_id": str(IDS["environment"]),
                "agent_id": str(IDS["agent"]),
            },
        )
        assert deployed.status_code == 201, deployed.text
        started = client.post(
            f"/v1/workflow-deployments/{deployed.json()['id']}/runs",
            headers=api_headers,
            json={
                "input": {"model_input": {"instruction": private_instruction}},
                "idempotency_key": f"agent-model-run-{suffix}",
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        assert private_instruction not in started.text

    workflow_worker = _worker(database_urls)
    assert _drain(workflow_worker) == 2
    with TestClient(app) as client:
        waiting = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert waiting.status_code == 200, waiting.text
        assert waiting.json()["status"] == "waiting"
        summary = waiting.json()["workflow"]["model_calls"][0]
        assert summary["status"] == "queued"
        assert private_instruction not in waiting.text
        unsafe_cancel = client.post(f"/v1/runs/{run_id}/cancel", headers=api_headers)
        assert unsafe_cancel.status_code == 409, unsafe_cancel.text

    owner_engine = create_engine(database_urls["owner"])
    with Session(owner_engine) as session:
        call = session.get(ModelCall, UUID(summary["id"]))
        assert call is not None
        assert call.encrypted_request.startswith("rsenc1:")
        assert private_instruction not in call.encrypted_request
        links = list(
            session.scalars(
                select(ModelCallBudgetReservation).where(
                    ModelCallBudgetReservation.model_call_id == call.id
                )
            )
        )
        assert links

    settings = WorkerSettings(worker_database_url=database_urls["worker"])  # type: ignore[call-arg]
    action_worker = ActionWorker(
        settings=settings,
        engine=create_engine(database_urls["worker"], pool_pre_ping=True),
    )
    claim = action_worker.model_call_worker.claim_ready()
    assert claim is not None
    assert str(claim.model_call_id) == summary["id"]
    with TestClient(app) as client:
        authorized = client.post(
            f"/internal/v1/model-calls/{claim.model_call_id}/authorize",
            headers={"X-RunSigil-Service-Token": os.environ["RUNSIGIL_GATEWAY_SERVICE_TOKEN"]},
            json={
                "content_digest": claim.content_digest,
                "claim_token": claim.claim_token,
                "mode": "execute",
            },
        )
        assert authorized.status_code == 200, authorized.text
        assert authorized.json()["model"] == "demo-governed-model"
        assert authorized.json()["request_digest"] == summary["request_digest"]
    action_worker.model_call_worker.settle(
        claim,
        ModelExecutionResult(outcome="ambiguous", error_code="model_response_unknown"),
    )
    assert action_worker.model_call_worker.claim_ready() is None
    with TestClient(app) as client:
        ambiguous = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert ambiguous.status_code == 200, ambiguous.text
        assert ambiguous.json()["status"] == "waiting"
        assert ambiguous.json()["workflow"]["model_calls"][0]["status"] == "reconciliation_required"

    with Session(owner_engine) as session, session.begin():
        call = session.get(ModelCall, claim.model_call_id)
        assert call is not None
        call.next_reconcile_at = database_now(session) - timedelta(seconds=1)

    reconcile_claim = action_worker.model_call_worker.claim_reconciliation()
    assert reconcile_claim is not None
    assert reconcile_claim.mode == "reconcile"
    assert reconcile_claim.model_call_id == claim.model_call_id
    assert reconcile_claim.idempotency_key == claim.idempotency_key
    with TestClient(app) as client:
        reconciled_authorization = client.post(
            f"/internal/v1/model-calls/{reconcile_claim.model_call_id}/authorize",
            headers={"X-RunSigil-Service-Token": os.environ["RUNSIGIL_GATEWAY_SERVICE_TOKEN"]},
            json={
                "content_digest": reconcile_claim.content_digest,
                "claim_token": reconcile_claim.claim_token,
                "mode": "reconcile",
            },
        )
        assert reconciled_authorization.status_code == 200, reconciled_authorization.text
    action_worker.model_call_worker.settle(
        reconcile_claim,
        ModelExecutionResult(
            outcome="completed",
            output={"status": "completed", "classification": "safe"},
            provider_reference=f"model-{suffix}",
            input_tokens=11,
            output_tokens=7,
            cost_minor=1,
        ),
    )
    assert _drain(workflow_worker) == 2

    with TestClient(app) as client:
        completed = client.get(f"/v1/runs/{run_id}", headers=api_headers)
        assert completed.status_code == 200, completed.text
        document = completed.json()
        assert document["status"] == "completed"
        assert document["workflow"]["path"] == ["start", "generate", "finish"]
        settled = document["workflow"]["model_calls"][0]
        assert settled["status"] == "completed"
        assert settled["output_digest"].startswith("sha256:")
        assert settled["input_tokens"] == 11
        assert private_instruction not in completed.text
        evidence = client.get(f"/v1/runs/{run_id}/evidence", headers=api_headers)
        assert evidence.status_code == 200, evidence.text
        evidence_call = evidence.json()["manifest"]["model_calls"][0]
        assert evidence_call["status"] == "completed"
        assert evidence_call["raw_content_captured"] is False
        assert private_instruction not in evidence.text
