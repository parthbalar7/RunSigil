from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from runsigil_gateway.main import app

from tests.unit.test_mcp_2026_protocol import (
    RUN_ID,
    FakeControlPlane,
    action_arguments,
)


@pytest.fixture
def a2a_gateway_client() -> Generator[tuple[TestClient, FakeControlPlane], None, None]:
    fake = FakeControlPlane()
    app.state.protocol_control_plane = fake
    with TestClient(app) as client:
        yield client, fake
    del app.state.protocol_control_plane


def a2a_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test-api-key",
        "Content-Type": "application/json",
        "A2A-Version": "1.0",
    }


def a2a_rpc(method: str, params: dict, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def start_message() -> dict:
    return {
        "message": {
            "messageId": "message-1",
            "role": "ROLE_USER",
            "parts": [
                {"data": {"operation": "runsigil.governed_action.start", **action_arguments()}}
            ],
        },
        "configuration": {
            "acceptedOutputModes": ["application/json"],
            "returnImmediately": True,
        },
    }


def test_agent_card_declares_only_implemented_a2a_1_features(
    a2a_gateway_client: tuple[TestClient, FakeControlPlane],
) -> None:
    client, _fake = a2a_gateway_client
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert card["capabilities"] == {
        "streaming": False,
        "pushNotifications": False,
        "extendedAgentCard": False,
    }
    assert card["defaultInputModes"] == ["application/json"]
    assert "kind" not in json.dumps(card)
    assert response.headers["etag"]

    cached = client.get(
        "/.well-known/agent-card.json", headers={"If-None-Match": response.headers["etag"]}
    )
    assert cached.status_code == 304


def test_send_and_continue_message_preserve_exact_content_approval(
    a2a_gateway_client: tuple[TestClient, FakeControlPlane],
) -> None:
    client, fake = a2a_gateway_client
    started = client.post(
        "/a2a/rpc",
        headers=a2a_headers(),
        json=a2a_rpc("SendMessage", start_message()),
    )
    assert started.status_code == 200
    task = started.json()["result"]["task"]
    assert task["id"] == str(RUN_ID)
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert task["status"]["message"]["parts"][0]["data"]["contentDigest"] == ("sha256:" + "b" * 64)
    assert fake.started_arguments is not None
    assert "organization_id" not in fake.started_arguments
    assert "sensitive-recipient@example.test" not in started.text
    assert "must-not-cross-protocol-boundary" not in started.text

    continued = client.post(
        "/a2a/rpc",
        headers=a2a_headers(),
        json=a2a_rpc(
            "SendMessage",
            {
                "message": {
                    "messageId": "message-2",
                    "taskId": str(RUN_ID),
                    "contextId": str(RUN_ID),
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "data": {
                                "operation": "runsigil.approval.decision",
                                "content_digest": "sha256:" + "b" * 64,
                                "decision": "approve",
                                "reason": "A2A contract test approval",
                            }
                        }
                    ],
                },
                "configuration": {"returnImmediately": True},
            },
            2,
        ),
    )
    assert continued.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert fake.decision is not None
    assert fake.decision["content_digest"] == "sha256:" + "b" * 64


def test_a2a_core_get_list_and_cancel_map_to_runs(
    a2a_gateway_client: tuple[TestClient, FakeControlPlane],
) -> None:
    client, fake = a2a_gateway_client
    fetched = client.post(
        "/a2a/rpc",
        headers=a2a_headers(),
        json=a2a_rpc("GetTask", {"id": str(RUN_ID)}),
    )
    assert fetched.json()["result"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"

    listed = client.post(
        "/a2a/rpc",
        headers=a2a_headers(),
        json=a2a_rpc("ListTasks", {"pageSize": 10, "includeArtifacts": False}),
    ).json()["result"]
    assert listed["pageSize"] == 1
    assert listed["totalSize"] == 1
    assert "artifacts" not in listed["tasks"][0]

    client.post(
        "/a2a/rpc",
        headers=a2a_headers(),
        json=a2a_rpc("ListTasks", {"status": "TASK_STATE_REJECTED"}),
    )
    assert fake.last_list_terminal_kind == "rejected"

    cancelled = client.post(
        "/a2a/rpc",
        headers=a2a_headers(),
        json=a2a_rpc("CancelTask", {"id": str(RUN_ID)}),
    )
    assert cancelled.json()["result"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert fake.run["status"] == "cancelled"


def test_a2a_rejects_legacy_version_unstructured_content_and_missing_auth(
    a2a_gateway_client: tuple[TestClient, FakeControlPlane],
) -> None:
    client, fake = a2a_gateway_client
    legacy = client.post(
        "/a2a/rpc",
        headers={"Authorization": "Bearer test-api-key", "Content-Type": "application/json"},
        json=a2a_rpc("GetTask", {"id": str(RUN_ID)}),
    )
    assert legacy.json()["error"]["code"] == -32009
    assert legacy.json()["error"]["data"][0]["metadata"]["supportedVersions"] == ["1.0"]

    text_message = start_message()
    text_message["message"]["parts"] = [{"text": "send the invoice"}]
    unsupported = client.post(
        "/a2a/rpc",
        headers=a2a_headers(),
        json=a2a_rpc("SendMessage", text_message),
    )
    assert unsupported.json()["error"]["code"] == -32005
    assert fake.started_arguments is None

    unauthenticated = client.post(
        "/a2a/rpc",
        headers={"Content-Type": "application/json", "A2A-Version": "1.0"},
        json=a2a_rpc("GetTask", {"id": str(RUN_ID)}),
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == "Bearer"
