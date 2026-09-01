from __future__ import annotations

import json
import os
import time
from typing import Any
from uuid import uuid4

import httpx
from runsigil_evidence import verify

POLICY_BUNDLE_ID = "70000000-0000-4000-8000-000000000001"


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


class Api:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.client.request(method, path, json=body)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError(f"RunSigil returned a non-object for {path}")
        return value

    def wait_for_run(self, run_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            run = self.request("GET", f"/v1/runs/{run_id}")
            if run["status"] in {"completed", "failed", "cancelled"}:
                return run
            time.sleep(0.5)
        raise TimeoutError(f"workflow run {run_id} did not reach a terminal state")

    def wait_for_evaluation(
        self,
        evaluation_id: str,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            evaluation = self.request("GET", f"/v1/evaluations/{evaluation_id}")
            if evaluation["status"] == "completed":
                return evaluation
            time.sleep(0.5)
        raise TimeoutError(f"evaluation {evaluation_id} did not complete")


def _limits() -> dict[str, int]:
    return {
        "max_steps": 10,
        "max_duration_seconds": 300,
        "max_tokens": 1_000,
        "max_cost_minor": 100,
    }


def _child_definition() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Child input"},
            {"id": "finish", "type": "output", "name": "Child output"},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "finish"}],
        "limits": _limits(),
    }


def _parent_definition(child_deployment_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {
                "id": "start",
                "type": "input",
                "name": "Policy checkpoint",
                "policy_bundle_id": POLICY_BUNDLE_ID,
            },
            {
                "id": "child",
                "type": "subworkflow",
                "name": "Referenced child",
                "config": {
                    "deployment_id": child_deployment_id,
                    "result_state_key": "child_result",
                },
                "timeout_seconds": 120,
            },
            {"id": "finish", "type": "output", "name": "Parent output"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "child"},
            {"id": "e2", "source": "child", "target": "finish"},
        ],
        "limits": _limits(),
    }


def _create_and_deploy(
    api: Api,
    *,
    project_id: str,
    environment_id: str,
    agent_id: str,
    slug: str,
    name: str,
    definition: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    workflow = api.request(
        "POST",
        "/v1/workflows",
        {
            "project_id": project_id,
            "slug": slug,
            "name": name,
            "definition": definition,
        },
    )
    version = workflow["latest_version"]
    deployment = api.request(
        "POST",
        f"/v1/workflow-versions/{version['id']}/deployments",
        {"environment_id": environment_id, "agent_id": agent_id},
    )
    return workflow, deployment


def main() -> None:
    suffix = uuid4().hex[:12]
    sensitive_value = f"phase-four-private-{suffix}"
    api = Api(_required("RUNSIGIL_API_URL"), _required("RUNSIGIL_API_KEY"))
    try:
        context = api.request("GET", "/v1/context")
        project_id = context["projects"][0]["id"]
        environment_id = context["environments"][0]["id"]
        agent_id = context["agents"][0]["id"]
        _child, child_deployment = _create_and_deploy(
            api,
            project_id=project_id,
            environment_id=environment_id,
            agent_id=agent_id,
            slug=f"phase-four-child-{suffix}",
            name="Phase four child",
            definition=_child_definition(),
        )
        parent, parent_deployment = _create_and_deploy(
            api,
            project_id=project_id,
            environment_id=environment_id,
            agent_id=agent_id,
            slug=f"phase-four-parent-{suffix}",
            name="Phase four governed parent",
            definition=_parent_definition(child_deployment["id"]),
        )
        input_value = {"case": sensitive_value}
        started = api.request(
            "POST",
            f"/v1/workflow-deployments/{parent_deployment['id']}/runs",
            {"input": input_value, "idempotency_key": f"phase-four-run-{suffix}"},
        )
        if sensitive_value in json.dumps(started):
            raise RuntimeError("raw workflow input was exposed by the Run API")
        completed = api.wait_for_run(started["id"])
        workflow = completed["workflow"]
        if completed["status"] != "completed" or workflow["path"] != [
            "start",
            "child",
            "finish",
        ]:
            raise RuntimeError(f"referenced workflow did not complete: {completed}")
        if workflow["subworkflows"][0]["status"] != "completed":
            raise RuntimeError("subworkflow call did not settle completed")
        if workflow["policy_decisions"][0]["effect"] != "allow":
            raise RuntimeError("per-node policy did not persist an allow decision")
        evidence = api.request("GET", f"/v1/runs/{completed['id']}/evidence")
        if not verify(evidence).valid or sensitive_value in json.dumps(evidence):
            raise RuntimeError("workflow evidence is invalid or contains raw state")

        replay_started = api.request(
            "POST",
            f"/v1/workflow-runs/{completed['id']}/replays",
            {
                "checkpoint_id": workflow["checkpoints"][0]["id"],
                "idempotency_key": f"phase-four-replay-{suffix}",
            },
        )
        replay_completed = api.wait_for_run(replay_started["id"])
        replay = replay_completed["workflow"]["replay"]
        if replay["status"] != "matched":
            raise RuntimeError(f"deterministic replay diverged: {replay}")

        expected_output = {"case": sensitive_value, "child_result": input_value}
        dataset = api.request(
            "POST",
            "/v1/evaluation-datasets",
            {
                "project_id": project_id,
                "slug": f"phase-four-evaluation-{suffix}",
                "name": "Phase four policy and safety evaluation",
                "scenarios": [
                    {
                        "key": "nested-safe",
                        "name": "Nested safe trajectory",
                        "input": input_value,
                        "expected_output": expected_output,
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
        evaluation = api.request(
            "POST",
            "/v1/evaluations",
            {
                "deployment_id": parent_deployment["id"],
                "dataset_version_id": dataset["version_id"],
                "idempotency_key": f"phase-four-evaluation-{suffix}",
                "minimum_score_milli": 1_000,
            },
        )
        evaluation = api.wait_for_evaluation(evaluation["id"])
        result = evaluation["results"][0]
        if (
            result["policy_outcome"] != "passed"
            or result["safety_outcome"] != "passed"
            or result["score_milli"] != 1_000
            or evaluation["release_gate_status"] != "passed"
        ):
            raise RuntimeError(f"policy/safety evaluation failed: {evaluation}")
        if sensitive_value in json.dumps(evaluation):
            raise RuntimeError("raw evaluation scenario was exposed")

        print(
            json.dumps(
                {
                    "status": "milestone_three_phase_four_live_proof_passed",
                    "parent_workflow_id": parent["id"],
                    "run_id": completed["id"],
                    "child_run_id": workflow["subworkflows"][0]["child_run_id"],
                    "policy_effect": workflow["policy_decisions"][0]["effect"],
                    "replay_run_id": replay_completed["id"],
                    "replay_status": replay["status"],
                    "evaluation_id": evaluation["id"],
                    "evaluation_score_milli": result["score_milli"],
                    "policy_outcome": result["policy_outcome"],
                    "safety_outcome": result["safety_outcome"],
                    "release_gate_status": evaluation["release_gate_status"],
                    "evidence_digest": evidence["content_digest"],
                    "raw_content_captured": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        api.close()


if __name__ == "__main__":
    main()
