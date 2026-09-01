from __future__ import annotations

import json
import os
import time
from typing import Any
from uuid import uuid4

import httpx
from runsigil_evidence import verify

POLICY_BUNDLE_ID = "70000000-0000-4000-8000-000000000001"
TOOL_ID = "60000000-0000-4000-8000-000000000001"
MODEL_ROUTE_ID = "60000000-0000-4000-8000-000000000002"


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

    def wait_for(
        self,
        run_id: str,
        *,
        terminal: bool,
        timeout_seconds: int = 90,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            run = self.request("GET", f"/v1/runs/{run_id}")
            if terminal and run["status"] in {"completed", "failed", "cancelled"}:
                return run
            workflow = run.get("workflow")
            if not terminal and isinstance(workflow, dict) and workflow.get("tool_calls"):
                return run
            time.sleep(0.5)
        raise TimeoutError(f"run {run_id} did not reach the expected state")


def _limits() -> dict[str, int]:
    return {
        "max_steps": 10,
        "max_duration_seconds": 300,
        "max_tokens": 1_000,
        "max_cost_minor": 100,
    }


def _tool_definition() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {
                "id": "send_invoice",
                "type": "tool",
                "name": "Governed invoice delivery",
                "policy_bundle_id": POLICY_BUNDLE_ID,
                "config": {
                    "tool_id": TOOL_ID,
                    "arguments_state_key": "invoice",
                    "result_state_key": "delivery",
                },
                "timeout_seconds": 120,
            },
            {"id": "finish", "type": "output", "name": "Output"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "send_invoice"},
            {"id": "e2", "source": "send_invoice", "target": "finish"},
        ],
        "limits": _limits(),
    }


def _agent_definition() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {
                "id": "generate",
                "type": "agent",
                "name": "Governed model generation",
                "model_route_id": MODEL_ROUTE_ID,
                "policy_bundle_id": POLICY_BUNDLE_ID,
                "config": {
                    "input_state_key": "model_input",
                    "result_state_key": "model_output",
                    "max_output_tokens": 128,
                },
                "timeout_seconds": 120,
            },
            {"id": "finish", "type": "output", "name": "Output"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "generate"},
            {"id": "e2", "source": "generate", "target": "finish"},
        ],
        "limits": _limits(),
    }


def _deploy(
    api: Api,
    *,
    suffix: str,
    name: str,
    definition: dict[str, Any],
    project_id: str,
    environment_id: str,
    agent_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    workflow = api.request(
        "POST",
        "/v1/workflows",
        {
            "project_id": project_id,
            "slug": f"{name}-{suffix}",
            "name": name.replace("-", " ").title(),
            "definition": definition,
        },
    )
    version = workflow["latest_version"]
    if not version["validation"]["executable"]:
        raise RuntimeError(f"workflow did not validate: {version['validation']}")
    deployment = api.request(
        "POST",
        f"/v1/workflow-versions/{version['id']}/deployments",
        {"environment_id": environment_id, "agent_id": agent_id},
    )
    return workflow, deployment


def main() -> None:
    suffix = uuid4().hex[:12]
    api = Api(_required("RUNSIGIL_API_URL"), _required("RUNSIGIL_API_KEY"))
    try:
        context = api.request("GET", "/v1/context")
        project_id = context["projects"][0]["id"]
        environment_id = context["environments"][0]["id"]
        agent_id = context["agents"][0]["id"]

        tool_workflow, tool_deployment = _deploy(
            api,
            suffix=suffix,
            name="phase-seven-simulation",
            definition=_tool_definition(),
            project_id=project_id,
            environment_id=environment_id,
            agent_id=agent_id,
        )
        recipient = f"phase-seven-private-{suffix}@example.test"
        source = api.request(
            "POST",
            f"/v1/workflow-deployments/{tool_deployment['id']}/runs",
            {
                "input": {
                    "invoice": {
                        "recipient": recipient,
                        "amount_cents": 2_100,
                        "description": "Simulation must perform no effect",
                        "simulate_outcome": "committed",
                    }
                },
                "idempotency_key": f"phase-seven-source-{suffix}",
            },
        )
        waiting = api.wait_for(source["id"], terminal=False)
        profile = api.request(
            "POST",
            "/v1/workflow-simulation-profiles",
            {
                "project_id": project_id,
                "tool_id": TOOL_ID,
                "name": f"phase-seven-deterministic-{suffix}",
            },
        )
        simulated = api.request(
            "POST",
            f"/v1/workflow-runs/{source['id']}/forks",
            {
                "checkpoint_id": waiting["workflow"]["checkpoints"][0]["id"],
                "simulation_profile_id": profile["id"],
                "idempotency_key": f"phase-seven-fork-{suffix}",
            },
        )
        simulation_run = api.wait_for(simulated["id"], terminal=True)
        simulation_evidence = api.request("GET", f"/v1/runs/{simulation_run['id']}/evidence")
        simulation_calls = simulation_run["workflow"]["tool_simulations"]
        if (
            simulation_run["status"] != "completed"
            or simulation_run["workflow"]["execution_mode"] != "simulation"
            or simulation_run["workflow"]["tool_calls"]
            or len(simulation_calls) != 1
            or simulation_evidence["manifest"]["tool_simulations"][0]["side_effect_performed"]
            is not False
            or not verify(simulation_evidence).valid
        ):
            raise RuntimeError("explicit effect simulation proof failed")
        if recipient in json.dumps(simulation_run) or recipient in json.dumps(simulation_evidence):
            raise RuntimeError("simulated arguments escaped the encrypted boundary")
        api.request("POST", f"/v1/runs/{source['id']}/cancel")

        agent_workflow, agent_deployment = _deploy(
            api,
            suffix=suffix,
            name="phase-seven-agent",
            definition=_agent_definition(),
            project_id=project_id,
            environment_id=environment_id,
            agent_id=agent_id,
        )
        private_instruction = f"private-model-input-{suffix}"
        agent_started = api.request(
            "POST",
            f"/v1/workflow-deployments/{agent_deployment['id']}/runs",
            {
                "input": {"model_input": {"instruction": private_instruction}},
                "idempotency_key": f"phase-seven-agent-{suffix}",
            },
        )
        agent_run = api.wait_for(agent_started["id"], terminal=True)
        agent_evidence = api.request("GET", f"/v1/runs/{agent_run['id']}/evidence")
        model_calls = agent_run["workflow"]["model_calls"]
        if (
            agent_run["status"] != "completed"
            or agent_run["workflow"]["path"] != ["start", "generate", "finish"]
            or len(model_calls) != 1
            or model_calls[0]["status"] != "completed"
            or model_calls[0]["input_tokens"] <= 0
            or model_calls[0]["output_tokens"] <= 0
            or agent_evidence["manifest"]["model_calls"][0]["raw_content_captured"] is not False
            or not verify(agent_evidence).valid
        ):
            raise RuntimeError("durable agent-model proof failed")
        if private_instruction in json.dumps(agent_run) or private_instruction in json.dumps(
            agent_evidence
        ):
            raise RuntimeError("model input escaped the encrypted boundary")

        print(
            json.dumps(
                {
                    "status": "milestone_three_phase_seven_live_proof_passed",
                    "simulation_workflow_id": tool_workflow["id"],
                    "simulation_run_id": simulation_run["id"],
                    "simulation_profile_id": profile["id"],
                    "side_effect_performed": False,
                    "simulation_evidence_digest": simulation_evidence["content_digest"],
                    "agent_workflow_id": agent_workflow["id"],
                    "agent_run_id": agent_run["id"],
                    "model_call_id": model_calls[0]["id"],
                    "model_provider_reference": model_calls[0]["provider_reference"],
                    "agent_evidence_digest": agent_evidence["content_digest"],
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
