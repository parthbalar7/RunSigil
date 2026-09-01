from __future__ import annotations

import json
import os
import time
from typing import Any
from uuid import uuid4

import httpx


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

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
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

    def wait_for_evaluation(self, evaluation_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            evaluation = self.request("GET", f"/v1/evaluations/{evaluation_id}")
            if evaluation["status"] in {"completed", "failed"}:
                return evaluation
            time.sleep(0.5)
        raise TimeoutError(f"evaluation {evaluation_id} did not reach a terminal state")


def _definition() -> dict[str, Any]:
    condition = {"field": "approved", "operator": "eq", "value": True}
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {"id": "route", "type": "condition", "name": "Route", "config": condition},
            {"id": "fan", "type": "parallel", "name": "Parallel checks"},
            {"id": "left", "type": "condition", "name": "Left", "config": condition},
            {"id": "right", "type": "condition", "name": "Right", "config": condition},
            {"id": "join", "type": "join", "name": "Deterministic join"},
            {
                "id": "loop",
                "type": "bounded_loop",
                "name": "Bounded loop",
                "config": {
                    "max_iterations": 2,
                    "max_duration_seconds": 30,
                    "max_tokens": 100,
                    "max_cost_minor": 10,
                },
            },
            {"id": "body", "type": "condition", "name": "Loop body", "config": condition},
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


def main() -> None:
    suffix = uuid4().hex[:12]
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
    api = Api(_required("RUNSIGIL_API_URL"), _required("RUNSIGIL_API_KEY"))
    try:
        context = api.request("GET", "/v1/context")
        project_id = context["projects"][0]["id"]
        environment_id = context["environments"][0]["id"]
        agent_id = context["agents"][0]["id"]
        workflow = api.request(
            "POST",
            "/v1/workflows",
            {
                "project_id": project_id,
                "slug": f"milestone-three-{suffix}",
                "name": "Milestone 3 live workflow",
                "description": "Durable deterministic workflow proof",
                "definition": _definition(),
            },
        )
        version = workflow["latest_version"]
        if not version["validation"]["executable"]:
            raise RuntimeError("live workflow did not validate as executable")
        deployment = api.request(
            "POST",
            f"/v1/workflow-versions/{version['id']}/deployments",
            {"environment_id": environment_id, "agent_id": agent_id},
        )
        input_value = {"approved": True, "scenario": "milestone-three-live"}
        started = api.request(
            "POST",
            f"/v1/workflow-deployments/{deployment['id']}/runs",
            {"input": input_value, "idempotency_key": f"m3-live-{suffix}"},
        )
        completed = api.wait_for_run(started["id"])
        if completed["status"] != "completed" or completed["workflow"]["path"] != expected_path:
            raise RuntimeError(
                f"workflow trajectory did not complete deterministically: {completed}"
            )
        evidence = api.request("GET", f"/v1/runs/{completed['id']}/evidence")
        checkpoint = completed["workflow"]["checkpoints"][2]
        forked = api.request(
            "POST",
            f"/v1/workflow-runs/{completed['id']}/forks",
            {"checkpoint_id": checkpoint["id"], "idempotency_key": f"m3-fork-{suffix}"},
        )
        fork_completed = api.wait_for_run(forked["id"])
        if fork_completed["workflow"]["path"] != expected_path:
            raise RuntimeError("checkpoint fork diverged from the deterministic path")
        dataset = api.request(
            "POST",
            "/v1/evaluation-datasets",
            {
                "project_id": project_id,
                "slug": f"milestone-three-{suffix}",
                "name": "Milestone 3 live dataset",
                "description": "Encrypted live scenario",
                "scenarios": [
                    {
                        "key": "approved",
                        "name": "Approved bounded trajectory",
                        "input": input_value,
                        "expected_output": input_value,
                        "expected_path": expected_path,
                        "metadata": {
                            "data_classification": "internal",
                            "tags": ["live-proof"],
                        },
                    }
                ],
            },
        )
        evaluation = api.request(
            "POST",
            "/v1/evaluations",
            {
                "deployment_id": deployment["id"],
                "dataset_version_id": dataset["version_id"],
                "idempotency_key": f"m3-eval-{suffix}",
                "minimum_score_milli": 1_000,
                "maximum_regression_milli": 0,
            },
        )
        evaluation = api.wait_for_evaluation(evaluation["id"])
        if evaluation["release_gate_status"] != "passed" or evaluation["score_milli"] != 1_000:
            raise RuntimeError(f"evaluation release gate failed: {evaluation}")
        print(
            json.dumps(
                {
                    "status": "milestone_three_foundation_live_proof_passed",
                    "workflow_id": workflow["id"],
                    "workflow_version_id": version["id"],
                    "deployment_id": deployment["id"],
                    "run_id": completed["id"],
                    "fork_run_id": fork_completed["id"],
                    "evaluation_id": evaluation["id"],
                    "executed_path": completed["workflow"]["path"],
                    "checkpoint_count": len(completed["workflow"]["checkpoints"]),
                    "evaluation_score_milli": evaluation["score_milli"],
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
