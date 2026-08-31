from __future__ import annotations

import json
from collections.abc import Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from runsigil_gateway.control_plane import ControlPlaneError, JsonObject
from runsigil_gateway.main import app

RUN_ID = UUID("10000000-0000-4000-8000-000000000001")
APPROVAL_ID = UUID("10000000-0000-4000-8000-000000000002")


def waiting_run() -> JsonObject:
    return {
        "id": str(RUN_ID),
        "status": "waiting_for_approval",
        "project_id": "20000000-0000-4000-8000-000000000001",
        "environment_id": "20000000-0000-4000-8000-000000000002",
        "agent_id": "20000000-0000-4000-8000-000000000003",
        "active_node": "human-approval",
        "input_digest": "sha256:" + "a" * 64,
        "created_at": "2026-08-31T12:00:00Z",
        "updated_at": "2026-08-31T12:00:01Z",
        "completed_at": None,
        "error_code": None,
        "action": {
            "id": "30000000-0000-4000-8000-000000000001",
            "tool_name": "demo.invoice.send",
            "state": "proposed",
            "receipt_preview": None,
            "error_code": None,
        },
        "approval": {
            "id": str(APPROVAL_ID),
            "status": "pending",
            "risk": "high",
            "content_digest": "sha256:" + "b" * 64,
            "expires_at": "2026-08-31T12:10:00Z",
            "request_preview": {
                "tool": "demo.invoice.send",
                "recipient": "se***@example.test",
                "amount_cents": 4200,
                "description": "must-not-cross-protocol-boundary",
                "binding": "exact-content",
            },
        },
        "trace_events": [],
        "evidence_status": "pending",
    }


class FakeControlPlane:
    def __init__(self) -> None:
        self.run = waiting_run()
        self.started_arguments: JsonObject | None = None
        self.decision: JsonObject | None = None
        self.authenticated = 0
        self.last_list_terminal_kind: str | None = None

    async def authenticate(self, authorization: str) -> None:
        assert authorization == "Bearer test-api-key"
        self.authenticated += 1

    async def start_run(self, authorization: str, arguments: JsonObject) -> JsonObject:
        assert authorization == "Bearer test-api-key"
        self.started_arguments = arguments
        return self.run

    async def get_run(self, authorization: str, run_id: UUID) -> JsonObject:
        assert authorization == "Bearer test-api-key"
        if run_id != RUN_ID:
            raise ControlPlaneError(404, "RUNSIGIL_NOT_FOUND", "Run not found.")
        return self.run

    async def list_runs(
        self,
        authorization: str,
        *,
        limit: int,
        cursor: str | None,
        statuses: list[str] | None,
        updated_after: str | None,
        terminal_kind: str | None,
    ) -> JsonObject:
        self.last_list_terminal_kind = terminal_kind
        return {"items": [self.run], "next_cursor": None, "page_size": 1, "total": 1}

    async def decide_approval(
        self,
        authorization: str,
        approval_id: UUID,
        decision: JsonObject,
    ) -> JsonObject:
        assert authorization == "Bearer test-api-key"
        assert approval_id == APPROVAL_ID
        self.decision = decision
        self.run["status"] = "completed" if decision["decision"] == "approve" else "cancelled"
        self.run["updated_at"] = "2026-08-31T12:00:02Z"
        self.run["approval"]["status"] = "approved"  # type: ignore[index]
        self.run["action"]["state"] = "committed"  # type: ignore[index]
        self.run["action"]["receipt_preview"] = {"status": "committed"}  # type: ignore[index]
        self.run["evidence_status"] = "local_only"
        return self.run

    async def cancel_run(self, authorization: str, run_id: UUID) -> JsonObject:
        assert authorization == "Bearer test-api-key"
        assert run_id == RUN_ID
        self.run["status"] = "cancelled"
        return self.run


@pytest.fixture
def gateway_client() -> Generator[tuple[TestClient, FakeControlPlane], None, None]:
    fake = FakeControlPlane()
    app.state.protocol_control_plane = fake
    with TestClient(app) as client:
        yield client, fake
    del app.state.protocol_control_plane


def metadata(*, tasks: bool = False) -> JsonObject:
    extensions = {"io.modelcontextprotocol/tasks": {}} if tasks else {}
    return {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "RunSigil test", "version": "1.0.0"},
        "io.modelcontextprotocol/clientCapabilities": {"extensions": extensions},
    }


def headers(method: str, *, name: str | None = None) -> dict[str, str]:
    values = {
        "Authorization": "Bearer test-api-key",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
    }
    if name is not None:
        values["Mcp-Name"] = name
    return values


def rpc(method: str, params: JsonObject, request_id: int = 1) -> JsonObject:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def action_arguments() -> JsonObject:
    return {
        "project_id": "20000000-0000-4000-8000-000000000001",
        "environment_id": "20000000-0000-4000-8000-000000000002",
        "agent_id": "20000000-0000-4000-8000-000000000003",
        "recipient": "sensitive-recipient@example.test",
        "amount_cents": 4200,
        "description": "sensitive-description",
        "idempotency_key": "mcp-test-idempotency",
    }


def test_discovery_is_stateless_and_advertises_tasks(
    gateway_client: tuple[TestClient, FakeControlPlane],
) -> None:
    client, fake = gateway_client
    response = client.post(
        "/mcp",
        headers=headers("server/discover"),
        json=rpc("server/discover", {"_meta": metadata()}),
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["supportedVersions"] == ["2026-07-28"]
    assert "io.modelcontextprotocol/tasks" in result["capabilities"]["extensions"]
    assert "Mcp-Session-Id" not in response.headers
    assert fake.authenticated == 1


def test_tool_call_and_task_approval_use_one_durable_run(
    gateway_client: tuple[TestClient, FakeControlPlane],
) -> None:
    client, fake = gateway_client
    started = client.post(
        "/mcp",
        headers=headers("tools/call", name="runsigil.governed_action.start"),
        json=rpc(
            "tools/call",
            {
                "name": "runsigil.governed_action.start",
                "arguments": action_arguments(),
                "_meta": metadata(tasks=True),
            },
        ),
    )
    assert started.status_code == 200
    assert started.json()["result"]["resultType"] == "task"
    assert started.json()["result"]["taskId"] == str(RUN_ID)
    assert fake.started_arguments is not None
    assert "organization_id" not in fake.started_arguments
    assert "sensitive-recipient@example.test" not in started.text

    task = client.post(
        "/mcp",
        headers=headers("tasks/get"),
        json=rpc("tasks/get", {"taskId": str(RUN_ID), "_meta": metadata(tasks=True)}, 2),
    )
    document = task.json()["result"]
    assert document["status"] == "input_required"
    schema = document["inputRequests"]["approval"]["params"]["requestedSchema"]
    assert schema["properties"]["content_digest"]["const"] == "sha256:" + "b" * 64
    assert "must-not-cross-protocol-boundary" not in json.dumps(document)

    updated = client.post(
        "/mcp",
        headers=headers("tasks/update"),
        json=rpc(
            "tasks/update",
            {
                "taskId": str(RUN_ID),
                "inputResponses": {
                    "approval": {
                        "action": "accept",
                        "content": {
                            "content_digest": "sha256:" + "b" * 64,
                            "decision": "approve",
                            "reason": "MCP contract test approval",
                        },
                    }
                },
                "_meta": metadata(tasks=True),
            },
            3,
        ),
    )
    assert updated.json()["result"] == {"resultType": "complete"}
    assert fake.decision is not None and fake.decision["content_digest"] == "sha256:" + "b" * 64

    completed = client.post(
        "/mcp",
        headers=headers("tasks/get"),
        json=rpc("tasks/get", {"taskId": str(RUN_ID), "_meta": metadata(tasks=True)}, 4),
    ).json()["result"]
    assert completed["status"] == "completed"
    assert completed["result"]["structuredContent"]["runId"] == str(RUN_ID)
    assert "sensitive-description" not in json.dumps(completed)


def test_transport_mismatches_capabilities_and_tenant_fields_fail_closed(
    gateway_client: tuple[TestClient, FakeControlPlane],
) -> None:
    client, fake = gateway_client
    mismatched = client.post(
        "/mcp",
        headers=headers("tools/list"),
        json=rpc("server/discover", {"_meta": metadata()}),
    )
    assert mismatched.status_code == 400
    assert mismatched.json()["error"]["code"] == -32020

    no_tasks = client.post(
        "/mcp",
        headers=headers("tools/call", name="runsigil.governed_action.start"),
        json=rpc(
            "tools/call",
            {
                "name": "runsigil.governed_action.start",
                "arguments": action_arguments(),
                "_meta": metadata(),
            },
        ),
    )
    assert no_tasks.json()["error"]["code"] == -32602

    with_tenant = action_arguments()
    with_tenant["organization_id"] = "90000000-0000-4000-8000-000000000001"
    rejected = client.post(
        "/mcp",
        headers=headers("tools/call", name="runsigil.governed_action.start"),
        json=rpc(
            "tools/call",
            {
                "name": "runsigil.governed_action.start",
                "arguments": with_tenant,
                "_meta": metadata(tasks=True),
            },
        ),
    )
    assert rejected.json()["error"]["code"] == -32602
    assert fake.started_arguments is None

    forbidden_origin = client.post(
        "/mcp",
        headers={**headers("server/discover"), "Origin": "https://attacker.example"},
        json=rpc("server/discover", {"_meta": metadata()}),
    )
    assert forbidden_origin.status_code == 403


def test_unknown_task_does_not_disclose_another_tenant(
    gateway_client: tuple[TestClient, FakeControlPlane],
) -> None:
    client, _fake = gateway_client
    response = client.post(
        "/mcp",
        headers=headers("tasks/get"),
        json=rpc(
            "tasks/get",
            {
                "taskId": "90000000-0000-4000-8000-000000000007",
                "_meta": metadata(tasks=True),
            },
        ),
    )
    assert response.status_code == 404
    assert response.json()["error"]["data"]["runsigilCode"] == "RUNSIGIL_NOT_FOUND"
    assert "tenant" not in response.text.lower()
