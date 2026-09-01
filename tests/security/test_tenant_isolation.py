from __future__ import annotations

import hashlib
import json
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
MODEL_ROUTE_B = UUID("90000000-0000-4000-8000-000000000009")
BUDGET_SCOPE_B = UUID("90000000-0000-4000-8000-000000000010")
WORKFLOW_B = UUID("90000000-0000-4000-8000-000000000011")
WORKFLOW_VERSION_B = UUID("90000000-0000-4000-8000-000000000012")
WORKFLOW_DEPLOYMENT_B = UUID("90000000-0000-4000-8000-000000000013")
WORKFLOW_EXECUTION_B = UUID("90000000-0000-4000-8000-000000000014")
WORKFLOW_WAIT_B = UUID("90000000-0000-4000-8000-000000000015")
EVALUATION_DATASET_B = UUID("90000000-0000-4000-8000-000000000016")
EVALUATION_DATASET_VERSION_B = UUID("90000000-0000-4000-8000-000000000017")
EVALUATION_SCENARIO_B = UUID("90000000-0000-4000-8000-000000000018")
EVALUATION_B = UUID("90000000-0000-4000-8000-000000000019")
EVALUATION_RUN_B = UUID("90000000-0000-4000-8000-000000000020")
EVALUATION_EXECUTION_B = UUID("90000000-0000-4000-8000-000000000021")
EVALUATION_RESULT_B = UUID("90000000-0000-4000-8000-000000000022")
EVALUATION_ANNOTATION_B = UUID("90000000-0000-4000-8000-000000000023")


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
                "INSERT INTO model_routes "
                "(id, organization_id, project_id, name, provider, model, status) "
                "VALUES (:id, :org, :project, 'private route', 'private', "
                "'private-model', 'active') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": MODEL_ROUTE_B, "org": ORG_B, "project": PROJECT_B},
        )
        connection.execute(
            text(
                "INSERT INTO budget_scopes "
                "(id, organization_id, scope_type, project_id) "
                "VALUES (:id, :org, 'project', :project) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": BUDGET_SCOPE_B, "org": ORG_B, "project": PROJECT_B},
        )
        definition = {
            "schema_version": 1,
            "entry_node_id": "start",
            "nodes": [
                {
                    "id": "start",
                    "type": "input",
                    "name": "Input",
                    "config": {},
                    "timeout_seconds": 300,
                    "retry_limit": 0,
                },
                {
                    "id": "done",
                    "type": "output",
                    "name": "Output",
                    "config": {},
                    "timeout_seconds": 300,
                    "retry_limit": 0,
                },
            ],
            "edges": [{"id": "edge", "source": "start", "target": "done", "branch": "default"}],
            "limits": {
                "max_steps": 10,
                "max_duration_seconds": 60,
                "max_tokens": 100,
                "max_cost_minor": 10,
            },
        }
        connection.execute(
            text(
                "INSERT INTO workflows "
                "(id, organization_id, project_id, slug, name, description) "
                "VALUES (:id, :org, :project, 'private-workflow', 'Private workflow', '') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": WORKFLOW_B, "org": ORG_B, "project": PROJECT_B},
        )
        connection.execute(
            text(
                "INSERT INTO workflow_versions "
                "(id, organization_id, workflow_id, version, status, definition_json, "
                "definition_digest, validation_json, created_by) "
                "VALUES (:id, :org, :workflow, 1, 'validated', CAST(:definition AS json), "
                ":digest, CAST('[]' AS json), :actor) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": WORKFLOW_VERSION_B,
                "org": ORG_B,
                "workflow": WORKFLOW_B,
                "definition": json.dumps(definition),
                "digest": digest,
                "actor": UUID("90000000-0000-4000-8000-000000000008"),
            },
        )
        connection.execute(
            text(
                "INSERT INTO runs "
                "(id, organization_id, project_id, environment_id, agent_id, actor_id, "
                "run_kind, status, idempotency_key, input_digest) "
                "VALUES (:id, :org, :project, :env, :agent, :actor, 'workflow', 'waiting', "
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
        connection.execute(
            text(
                "INSERT INTO workflow_deployments "
                "(id, organization_id, workflow_version_id, environment_id, agent_id, "
                "status, deployed_by, deployed_at) "
                "VALUES (:id, :org, :version, :env, :agent, 'active', :actor, now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": WORKFLOW_DEPLOYMENT_B,
                "org": ORG_B,
                "version": WORKFLOW_VERSION_B,
                "env": ENV_B,
                "agent": AGENT_B,
                "actor": UUID("90000000-0000-4000-8000-000000000008"),
            },
        )
        connection.execute(
            text(
                "INSERT INTO workflow_executions "
                "(id, organization_id, run_id, workflow_version_id, deployment_id, status, "
                "version, content_digest, encrypted_state, state_digest, current_nodes_json, "
                "completed_nodes_json, path_json, loop_counts_json, step_count, max_steps, "
                "deadline_at) VALUES (:id, :org, :run, :version_id, :deployment, 'waiting', "
                "1, :digest, 'rsenc1:private', :digest, CAST('[\"approval\"]' AS json), "
                "CAST('[]' AS json), CAST('[]' AS json), CAST('{}' AS json), 0, 10, "
                "now() + interval '10 minutes') ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": WORKFLOW_EXECUTION_B,
                "org": ORG_B,
                "run": RUN_B,
                "version_id": WORKFLOW_VERSION_B,
                "deployment": WORKFLOW_DEPLOYMENT_B,
                "digest": digest,
            },
        )
        connection.execute(
            text(
                "INSERT INTO workflow_waits "
                "(id, organization_id, workflow_execution_id, run_id, node_id, sequence, "
                "wait_type, status, content_digest, state_digest, request_metadata_json, "
                "expires_at) VALUES (:id, :org, :execution, :run, 'approval', 0, "
                "'approval', 'pending', :digest, :digest, "
                'CAST(\'{"risk":"high","reason_code":"private"}\' AS json), '
                "now() + interval '10 minutes') ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": WORKFLOW_WAIT_B,
                "org": ORG_B,
                "execution": WORKFLOW_EXECUTION_B,
                "run": RUN_B,
                "digest": digest,
            },
        )
        connection.execute(
            text(
                "INSERT INTO evaluation_datasets "
                "(id, organization_id, project_id, slug, name, description) "
                "VALUES (:id, :org, :project, 'private-evaluation', "
                "'Private evaluation', '') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": EVALUATION_DATASET_B, "org": ORG_B, "project": PROJECT_B},
        )
        connection.execute(
            text(
                "INSERT INTO evaluation_dataset_versions "
                "(id, organization_id, dataset_id, version, status, content_digest, "
                "scenario_count, created_by) VALUES (:id, :org, :dataset, 1, 'active', "
                ":digest, 1, :actor) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": EVALUATION_DATASET_VERSION_B,
                "org": ORG_B,
                "dataset": EVALUATION_DATASET_B,
                "digest": digest,
                "actor": UUID("90000000-0000-4000-8000-000000000008"),
            },
        )
        connection.execute(
            text(
                "INSERT INTO evaluation_scenarios "
                "(id, organization_id, dataset_version_id, scenario_key, name, "
                "encrypted_payload, input_digest, expected_output_digest, "
                "expected_path_json, metadata_json, content_digest) VALUES "
                "(:id, :org, :version, 'private-scenario', 'Private scenario', "
                "'rsenc1:private', :digest, :digest, CAST('[]' AS json), "
                "CAST('{}' AS json), :digest) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": EVALUATION_SCENARIO_B,
                "org": ORG_B,
                "version": EVALUATION_DATASET_VERSION_B,
                "digest": digest,
            },
        )
        connection.execute(
            text(
                "INSERT INTO evaluations "
                "(id, organization_id, workflow_version_id, dataset_version_id, "
                "deployment_id, actor_id, idempotency_key, status, minimum_score_milli, "
                "maximum_regression_milli, score_milli, regression_status, "
                "release_gate_status, content_digest, completed_at) VALUES "
                "(:id, :org, :workflow_version, :dataset_version, :deployment, :actor, "
                "'org-b-private-evaluation', 'completed', 1000, 0, 1000, 'not_compared', "
                "'passed', :digest, now()) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": EVALUATION_B,
                "org": ORG_B,
                "workflow_version": WORKFLOW_VERSION_B,
                "dataset_version": EVALUATION_DATASET_VERSION_B,
                "deployment": WORKFLOW_DEPLOYMENT_B,
                "actor": UUID("90000000-0000-4000-8000-000000000008"),
                "digest": digest,
            },
        )
        connection.execute(
            text(
                "INSERT INTO runs "
                "(id, organization_id, project_id, environment_id, agent_id, actor_id, "
                "run_kind, status, idempotency_key, input_digest, completed_at) VALUES "
                "(:id, :org, :project, :env, :agent, :actor, 'workflow', 'completed', "
                "'org-b-private-evaluation-run', :digest, now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": EVALUATION_RUN_B,
                "org": ORG_B,
                "project": PROJECT_B,
                "env": ENV_B,
                "agent": AGENT_B,
                "actor": UUID("90000000-0000-4000-8000-000000000008"),
                "digest": digest,
            },
        )
        connection.execute(
            text(
                "INSERT INTO workflow_executions "
                "(id, organization_id, run_id, workflow_version_id, deployment_id, "
                "evaluation_id, evaluation_scenario_id, status, version, content_digest, "
                "encrypted_state, state_digest, current_nodes_json, completed_nodes_json, "
                "path_json, loop_counts_json, step_count, max_steps, deadline_at, "
                "completed_at) VALUES (:id, :org, :run, :workflow_version, :deployment, "
                ":evaluation, :scenario, 'completed', 1, :digest, 'rsenc1:private', "
                ":digest, CAST('[]' AS json), CAST('[\"done\"]' AS json), "
                "CAST('[\"done\"]' AS json), CAST('{}' AS json), 1, 10, "
                "now() + interval '10 minutes', now()) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": EVALUATION_EXECUTION_B,
                "org": ORG_B,
                "run": EVALUATION_RUN_B,
                "workflow_version": WORKFLOW_VERSION_B,
                "deployment": WORKFLOW_DEPLOYMENT_B,
                "evaluation": EVALUATION_B,
                "scenario": EVALUATION_SCENARIO_B,
                "digest": digest,
            },
        )
        connection.execute(
            text(
                "INSERT INTO evaluation_results "
                "(id, organization_id, evaluation_id, scenario_id, workflow_execution_id, "
                "run_id, status, score_milli, task_outcome, trajectory_outcome, "
                "deterministic_environment_outcome, policy_outcome, safety_outcome, "
                "output_digest, trajectory_digest, "
                "graders_json) VALUES (:id, :org, :evaluation, :scenario, :execution, "
                ":run, 'passed', 1000, 'passed', 'passed', 'passed', 'passed', 'passed', "
                ":digest, :digest, "
                "CAST('[]' AS json)) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": EVALUATION_RESULT_B,
                "org": ORG_B,
                "evaluation": EVALUATION_B,
                "scenario": EVALUATION_SCENARIO_B,
                "execution": EVALUATION_EXECUTION_B,
                "run": EVALUATION_RUN_B,
                "digest": digest,
            },
        )
        connection.execute(
            text(
                "INSERT INTO evaluation_annotations "
                "(id, organization_id, evaluation_result_id, evaluation_id, scenario_id, "
                "run_id, reviewer_id, idempotency_key, label, score_milli, "
                "reason_codes_json, content_digest) VALUES (:id, :org, :result, "
                ":evaluation, :scenario, :run, :reviewer, 'org-b-private-annotation', "
                "'passed', 1000, CAST('[\"private_review\"]' AS json), :digest) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": EVALUATION_ANNOTATION_B,
                "org": ORG_B,
                "result": EVALUATION_RESULT_B,
                "evaluation": EVALUATION_B,
                "scenario": EVALUATION_SCENARIO_B,
                "run": EVALUATION_RUN_B,
                "reviewer": UUID("90000000-0000-4000-8000-000000000008"),
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
        workflow = client.get(f"/v1/workflows/{WORKFLOW_B}", headers=api_headers)
        workflow_wait = client.get(f"/v1/workflow-waits/{WORKFLOW_WAIT_B}", headers=api_headers)
        annotation = client.post(
            f"/v1/evaluation-results/{EVALUATION_RESULT_B}/annotations",
            headers=api_headers,
            json={
                "idempotency_key": "cross-tenant-review-attempt",
                "label": "passed",
                "reason_codes": ["should_not_exist"],
            },
        )
    assert response.status_code == 404
    assert listed.status_code == 200
    assert str(RUN_B) not in {row["id"] for row in listed.json()["items"]}
    assert cancelled.status_code == 404
    assert workflow.status_code == 404
    assert workflow_wait.status_code == 404
    assert annotation.status_code == 404

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
        assert connection.scalar(text("SELECT count(*) FROM model_routes")) == 1
        assert connection.scalar(text("SELECT count(*) FROM budget_scopes")) == 6
        assert (
            connection.scalar(
                text("SELECT count(*) FROM workflows WHERE id = :id"), {"id": WORKFLOW_B}
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM workflow_waits WHERE id = :id"),
                {"id": WORKFLOW_WAIT_B},
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM evaluation_annotations WHERE id = :id"),
                {"id": EVALUATION_ANNOTATION_B},
            )
            == 0
        )

    owner_engine = create_engine(database_urls["owner"])
    with owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname IN ('model_routes','budget_scopes',"
                "'action_budget_reservations','dead_letters','workflows',"
                "'workflow_versions','workflow_deployments','workflow_executions',"
                "'workflow_node_attempts','run_checkpoints','evaluation_datasets',"
                "'evaluation_dataset_versions','evaluation_scenarios','evaluations',"
                "'evaluation_results','workflow_waits','evaluation_annotations',"
                "'workflow_subworkflow_calls','workflow_tool_calls','workflow_policy_decisions',"
                "'workflow_replays','workflow_simulation_profiles',"
                "'workflow_tool_simulation_calls','model_calls',"
                "'model_call_budget_reservations')"
            )
        ).all()
    assert {row.relname for row in rows} == {
        "model_routes",
        "budget_scopes",
        "action_budget_reservations",
        "dead_letters",
        "workflows",
        "workflow_versions",
        "workflow_deployments",
        "workflow_executions",
        "workflow_node_attempts",
        "run_checkpoints",
        "evaluation_datasets",
        "evaluation_dataset_versions",
        "evaluation_scenarios",
        "evaluations",
        "evaluation_results",
        "workflow_waits",
        "evaluation_annotations",
        "workflow_subworkflow_calls",
        "workflow_tool_calls",
        "workflow_policy_decisions",
        "workflow_replays",
        "workflow_simulation_profiles",
        "workflow_tool_simulation_calls",
        "model_calls",
        "model_call_budget_reservations",
    }
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)
