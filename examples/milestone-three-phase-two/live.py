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

    def expect_conflict(self, path: str, body: dict[str, Any]) -> None:
        response = self.client.post(path, json=body)
        if response.status_code != 409:
            raise RuntimeError(
                f"expected fail-closed 409 from {path}, got {response.status_code}: {response.text}"
            )

    def wait_for_pending_wait(
        self,
        run_id: str,
        wait_type: str,
        timeout_seconds: int = 45,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            run = self.request("GET", f"/v1/runs/{run_id}")
            waits = run.get("workflow", {}).get("waits", [])
            match = next(
                (
                    wait
                    for wait in waits
                    if wait["wait_type"] == wait_type and wait["status"] == "pending"
                ),
                None,
            )
            if match is not None:
                return run, match
            if run["status"] in {"completed", "failed", "cancelled"}:
                raise RuntimeError(f"run became terminal before {wait_type} wait: {run}")
            time.sleep(0.25)
        raise TimeoutError(f"workflow run {run_id} did not reach a {wait_type} wait")

    def wait_for_run(self, run_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            run = self.request("GET", f"/v1/runs/{run_id}")
            if run["status"] in {"completed", "failed", "cancelled"}:
                return run
            time.sleep(0.25)
        raise TimeoutError(f"workflow run {run_id} did not reach a terminal state")

    def wait_for_evaluation(
        self,
        evaluation_id: str,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            evaluation = self.request("GET", f"/v1/evaluations/{evaluation_id}")
            if evaluation["status"] in {"completed", "failed"}:
                return evaluation
            time.sleep(0.25)
        raise TimeoutError(f"evaluation {evaluation_id} did not reach a terminal state")


def _wait_definition() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {
                "id": "settle_timer",
                "type": "timer",
                "name": "Settlement delay",
                "timeout_seconds": 10,
                "config": {"delay_seconds": 1},
            },
            {
                "id": "review",
                "type": "approval",
                "name": "Operator review",
                "timeout_seconds": 30,
                "config": {"risk": "high", "reason_code": "operator_review"},
            },
            {
                "id": "details",
                "type": "request_information",
                "name": "Request details",
                "timeout_seconds": 30,
                "config": {
                    "reason_code": "ticket_context_required",
                    "state_key": "review_context",
                },
            },
            {
                "id": "ticket_event",
                "type": "event",
                "name": "Ticket event",
                "timeout_seconds": 30,
                "config": {
                    "event_key": "ticket_closed",
                    "state_key": "ticket_event",
                },
            },
            {"id": "done", "type": "output", "name": "Completed"},
            {"id": "denied", "type": "output", "name": "Denied"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "settle_timer"},
            {"id": "e2", "source": "settle_timer", "target": "review"},
            {
                "id": "e3",
                "source": "review",
                "target": "details",
                "branch": "approved",
            },
            {
                "id": "e4",
                "source": "review",
                "target": "denied",
                "branch": "denied",
            },
            {"id": "e5", "source": "details", "target": "ticket_event"},
            {"id": "e6", "source": "ticket_event", "target": "done"},
        ],
        "limits": {
            "max_steps": 20,
            "max_duration_seconds": 120,
            "max_tokens": 100,
            "max_cost_minor": 10,
        },
    }


def _evaluation_definition() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "input", "name": "Input"},
            {"id": "done", "type": "output", "name": "Output"},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "done"}],
        "limits": {
            "max_steps": 5,
            "max_duration_seconds": 30,
            "max_tokens": 100,
            "max_cost_minor": 10,
        },
    }


def _create_deployment(
    api: Api,
    *,
    project_id: str,
    environment_id: str,
    agent_id: str,
    suffix: str,
    purpose: str,
    definition: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    workflow = api.request(
        "POST",
        "/v1/workflows",
        {
            "project_id": project_id,
            "slug": f"m3-phase-two-{purpose}-{suffix}",
            "name": f"Milestone 3 phase two {purpose}",
            "description": "Live phase-two proof",
            "definition": definition,
        },
    )
    version = workflow["latest_version"]
    if not version["validation"]["executable"]:
        raise RuntimeError(f"{purpose} workflow did not validate: {version['validation']}")
    deployment = api.request(
        "POST",
        f"/v1/workflow-versions/{version['id']}/deployments",
        {"environment_id": environment_id, "agent_id": agent_id},
    )
    return workflow, version, deployment


def main() -> None:
    suffix = uuid4().hex[:12]
    information_value = "phase-two-private-information"
    event_value = "phase-two-private-event"
    api = Api(_required("RUNSIGIL_API_URL"), _required("RUNSIGIL_API_KEY"))
    try:
        context = api.request("GET", "/v1/context")
        project_id = context["projects"][0]["id"]
        environment_id = context["environments"][0]["id"]
        agent_id = context["agents"][0]["id"]

        workflow, version, deployment = _create_deployment(
            api,
            project_id=project_id,
            environment_id=environment_id,
            agent_id=agent_id,
            suffix=suffix,
            purpose="waits",
            definition=_wait_definition(),
        )
        started = api.request(
            "POST",
            f"/v1/workflow-deployments/{deployment['id']}/runs",
            {
                "input": {"case": "phase-two-live"},
                "idempotency_key": f"m3-phase-two-waits-{suffix}",
            },
        )

        _, approval = api.wait_for_pending_wait(started["id"], "approval")
        api.expect_conflict(
            f"/v1/workflow-waits/{approval['id']}/decision",
            {
                "content_digest": f"sha256:{'0' * 64}",
                "decision": "approved",
            },
        )
        api.request(
            "POST",
            f"/v1/workflow-waits/{approval['id']}/decision",
            {"content_digest": approval["content_digest"], "decision": "approved"},
        )
        api.expect_conflict(
            f"/v1/workflow-waits/{approval['id']}/decision",
            {"content_digest": approval["content_digest"], "decision": "approved"},
        )

        _, information = api.wait_for_pending_wait(started["id"], "request_information")
        api.request(
            "POST",
            f"/v1/workflow-waits/{information['id']}/information",
            {
                "content_digest": information["content_digest"],
                "information": {"private_note": information_value},
            },
        )

        _, event = api.wait_for_pending_wait(started["id"], "event")
        api.expect_conflict(
            f"/v1/workflow-waits/{event['id']}/event",
            {
                "content_digest": event["content_digest"],
                "event_key": "wrong_event",
                "payload": {"private_receipt": event_value},
            },
        )
        api.request(
            "POST",
            f"/v1/workflow-waits/{event['id']}/event",
            {
                "content_digest": event["content_digest"],
                "event_key": "ticket_closed",
                "payload": {"private_receipt": event_value},
            },
        )
        completed = api.wait_for_run(started["id"])
        expected_path = ["start", "settle_timer", "review", "details", "ticket_event", "done"]
        if completed["status"] != "completed" or completed["workflow"]["path"] != expected_path:
            raise RuntimeError(f"serial wait workflow did not complete: {completed}")
        resolutions = {
            wait["wait_type"]: (wait["status"], wait["resolution"])
            for wait in completed["workflow"]["waits"]
        }
        if resolutions != {
            "timer": ("resolved", "elapsed"),
            "approval": ("resolved", "approved"),
            "request_information": ("resolved", "received"),
            "event": ("resolved", "received"),
        }:
            raise RuntimeError(f"unexpected wait resolutions: {resolutions}")
        evidence = api.request("GET", f"/v1/runs/{completed['id']}/evidence")
        exposed = json.dumps({"run": completed, "evidence": evidence}, sort_keys=True)
        if information_value in exposed or event_value in exposed:
            raise RuntimeError("raw wait response escaped a metadata-only boundary")

        _, _, evaluation_deployment = _create_deployment(
            api,
            project_id=project_id,
            environment_id=environment_id,
            agent_id=agent_id,
            suffix=suffix,
            purpose="evaluation",
            definition=_evaluation_definition(),
        )
        scenario_input = {"case": "human-annotation-live"}
        dataset = api.request(
            "POST",
            "/v1/evaluation-datasets",
            {
                "project_id": project_id,
                "slug": f"m3-phase-two-{suffix}",
                "name": "Milestone 3 phase two annotation dataset",
                "description": "Encrypted scenario for a human review proof",
                "scenarios": [
                    {
                        "key": "human-review",
                        "name": "Human review scenario",
                        "input": scenario_input,
                        "expected_output": scenario_input,
                        "expected_path": ["start", "done"],
                        "metadata": {
                            "data_classification": "internal",
                            "tags": ["phase-two-live"],
                        },
                    }
                ],
            },
        )
        evaluation = api.request(
            "POST",
            "/v1/evaluations",
            {
                "deployment_id": evaluation_deployment["id"],
                "dataset_version_id": dataset["version_id"],
                "idempotency_key": f"m3-phase-two-eval-{suffix}",
                "minimum_score_milli": 1_000,
                "maximum_regression_milli": 0,
            },
        )
        evaluation = api.wait_for_evaluation(evaluation["id"])
        if evaluation["release_gate_status"] != "passed":
            raise RuntimeError(f"evaluation release gate failed: {evaluation}")
        result_id = evaluation["results"][0]["id"]
        annotation_body = {
            "idempotency_key": f"m3-phase-two-review-{suffix}",
            "label": "passed",
            "score_milli": 1000,
            "reason_codes": ["human_verified"],
        }
        annotation = api.request(
            "POST",
            f"/v1/evaluation-results/{result_id}/annotations",
            annotation_body,
        )
        replayed_annotation = api.request(
            "POST",
            f"/v1/evaluation-results/{result_id}/annotations",
            annotation_body,
        )
        if annotation["id"] != replayed_annotation["id"]:
            raise RuntimeError("annotation idempotency replay created another record")
        reviewed_evaluation = api.request("GET", f"/v1/evaluations/{evaluation['id']}")
        annotations = reviewed_evaluation["results"][0]["annotations"]
        if [item["id"] for item in annotations] != [annotation["id"]]:
            raise RuntimeError("append-only annotation was not returned with its result")

        print(
            json.dumps(
                {
                    "status": "milestone_three_phase_two_live_proof_passed",
                    "workflow_id": workflow["id"],
                    "workflow_version_id": version["id"],
                    "run_id": completed["id"],
                    "executed_path": completed["workflow"]["path"],
                    "wait_resolutions": resolutions,
                    "evaluation_id": evaluation["id"],
                    "evaluation_result_id": result_id,
                    "annotation_id": annotation["id"],
                    "annotation_idempotency_proven": True,
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
