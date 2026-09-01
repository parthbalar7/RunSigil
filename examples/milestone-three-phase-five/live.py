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

    def wait_for_tool_call(self, run_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            run = self.request("GET", f"/v1/runs/{run_id}")
            workflow = run.get("workflow")
            if isinstance(workflow, dict) and workflow.get("tool_calls"):
                return run
            if run["status"] in {"failed", "cancelled", "completed"}:
                raise RuntimeError(f"workflow terminated before its tool call: {run}")
            time.sleep(0.5)
        raise TimeoutError(f"workflow run {run_id} did not create its tool call")

    def wait_for_run(self, run_id: str, timeout_seconds: int = 90) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            run = self.request("GET", f"/v1/runs/{run_id}")
            if run["status"] in {"completed", "failed", "cancelled"}:
                return run
            time.sleep(0.5)
        raise TimeoutError(f"run {run_id} did not reach a terminal state")


def _definition() -> dict[str, Any]:
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
        "limits": {
            "max_steps": 10,
            "max_duration_seconds": 300,
            "max_tokens": 1_000,
            "max_cost_minor": 100,
        },
    }


def main() -> None:
    suffix = uuid4().hex[:12]
    recipient = f"phase-five-private-{suffix}@example.test"
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
                "slug": f"phase-five-tool-{suffix}",
                "name": "Phase five governed tool",
                "definition": _definition(),
            },
        )
        version = workflow["latest_version"]
        if not version["validation"]["executable"]:
            raise RuntimeError(f"tool workflow did not validate: {version['validation']}")
        deployment = api.request(
            "POST",
            f"/v1/workflow-versions/{version['id']}/deployments",
            {"environment_id": environment_id, "agent_id": agent_id},
        )
        started = api.request(
            "POST",
            f"/v1/workflow-deployments/{deployment['id']}/runs",
            {
                "input": {
                    "invoice": {
                        "recipient": recipient,
                        "amount_cents": 3_600,
                        "description": "Phase five live governed delivery",
                        "simulate_outcome": "committed",
                    }
                },
                "idempotency_key": f"phase-five-tool-{suffix}",
            },
        )
        if recipient in json.dumps(started):
            raise RuntimeError("workflow start response exposed raw tool arguments")
        waiting = api.wait_for_tool_call(started["id"])
        call = waiting["workflow"]["tool_calls"][0]
        if call["status"] != "pending_approval":
            raise RuntimeError(f"tool call skipped exact approval: {call}")
        child = api.request("GET", f"/v1/runs/{call['child_run_id']}")
        approval = child.get("approval")
        if not isinstance(approval, dict) or approval.get("status") != "pending":
            raise RuntimeError("governed child approval was not created")
        if recipient in json.dumps(waiting) or recipient in json.dumps(child):
            raise RuntimeError("tool arguments escaped the encrypted child boundary")
        api.request(
            "POST",
            f"/v1/approvals/{approval['id']}/decision",
            {
                "content_digest": approval["content_digest"],
                "decision": "approve",
                "reason": "Approved by the Milestone 3 phase five live proof",
            },
        )
        completed = api.wait_for_run(started["id"])
        child_completed = api.wait_for_run(call["child_run_id"])
        settled = completed["workflow"]["tool_calls"][0]
        if (
            completed["status"] != "completed"
            or completed["workflow"]["path"] != ["start", "send_invoice", "finish"]
            or settled["status"] != "completed"
            or child_completed["action"]["state"] != "committed"
        ):
            raise RuntimeError(f"governed workflow tool did not complete: {completed}")
        child_evidence = api.request("GET", f"/v1/runs/{call['child_run_id']}/evidence")
        parent_evidence = api.request("GET", f"/v1/runs/{started['id']}/evidence")
        if not verify(child_evidence).valid or not verify(parent_evidence).valid:
            raise RuntimeError("child or parent evidence signature did not verify")
        evidence_call = parent_evidence["manifest"]["tool_calls"][0]
        if evidence_call["child_evidence_digest"] != child_evidence["content_digest"]:
            raise RuntimeError("parent evidence did not bind the exact child evidence")
        if recipient in json.dumps(completed) or recipient in json.dumps(parent_evidence):
            raise RuntimeError("raw tool arguments were captured in parent output or evidence")
        print(
            json.dumps(
                {
                    "status": "milestone_three_phase_five_live_proof_passed",
                    "workflow_id": workflow["id"],
                    "parent_run_id": completed["id"],
                    "child_run_id": child_completed["id"],
                    "workflow_tool_call_id": settled["id"],
                    "action_id": settled["action_id"],
                    "approval_digest": approval["content_digest"],
                    "action_state": child_completed["action"]["state"],
                    "parent_evidence_digest": parent_evidence["content_digest"],
                    "child_evidence_digest": child_evidence["content_digest"],
                    "child_evidence_linked": True,
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
