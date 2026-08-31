from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from uuid import uuid4

import httpx

JsonObject = dict[str, Any]
MCP_VERSION = "2026-07-28"
TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
MCP_TOOL = "runsigil.governed_action.start"


def _json(response: httpx.Response) -> JsonObject:
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"HTTP {response.status_code} returned non-JSON content") from exc
    if not response.is_success:
        raise RuntimeError(f"HTTP {response.status_code}: {json.dumps(body, sort_keys=True)}")
    if not isinstance(body, dict):
        raise RuntimeError("Protocol response is not a JSON object")
    return body


def _meta() -> JsonObject:
    return {
        "io.modelcontextprotocol/protocolVersion": MCP_VERSION,
        "io.modelcontextprotocol/clientInfo": {
            "name": "runsigil-live-proof",
            "version": "0.2.0",
        },
        "io.modelcontextprotocol/clientCapabilities": {"extensions": {TASKS_EXTENSION: {}}},
    }


def _mcp(
    client: httpx.Client,
    api_key: str,
    method: str,
    params: JsonObject,
    request_id: int,
    *,
    name: str | None = None,
) -> JsonObject:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_VERSION,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    document = _json(
        client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": {**params, "_meta": _meta()},
            },
        )
    )
    if "error" in document:
        raise RuntimeError(f"MCP {method} failed: {json.dumps(document['error'], sort_keys=True)}")
    result = document.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"MCP {method} returned an invalid result")
    return result


def _a2a(
    client: httpx.Client,
    api_key: str,
    method: str,
    params: JsonObject,
    request_id: int,
) -> JsonObject:
    document = _json(
        client.post(
            "/a2a/rpc",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "A2A-Version": "1.0",
            },
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        )
    )
    if "error" in document:
        raise RuntimeError(f"A2A {method} failed: {json.dumps(document['error'], sort_keys=True)}")
    result = document.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"A2A {method} returned an invalid result")
    return result


def _arguments(context: JsonObject, prefix: str, recipient: str) -> JsonObject:
    return {
        "project_id": context["projects"][0]["id"],
        "environment_id": context["environments"][0]["id"],
        "agent_id": context["agents"][0]["id"],
        "recipient": recipient,
        "amount_cents": 4200,
        "description": f"{prefix} protocol live proof",
        "idempotency_key": f"{prefix}-{uuid4()}",
        "simulate_outcome": "committed",
    }


def _poll_mcp(client: httpx.Client, api_key: str, task_id: str) -> JsonObject:
    deadline = time.monotonic() + 45
    request_id = 20
    while time.monotonic() < deadline:
        task = _mcp(client, api_key, "tasks/get", {"taskId": task_id}, request_id)
        request_id += 1
        if task.get("status") in {"completed", "failed", "cancelled"}:
            return task
        time.sleep(0.5)
    raise RuntimeError(f"MCP task {task_id} did not finish within 45 seconds")


def _poll_a2a(client: httpx.Client, api_key: str, task_id: str) -> JsonObject:
    deadline = time.monotonic() + 45
    request_id = 120
    while time.monotonic() < deadline:
        task = _a2a(client, api_key, "GetTask", {"id": task_id}, request_id)
        request_id += 1
        state = task.get("status", {}).get("state")
        if state in {
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED",
            "TASK_STATE_REJECTED",
        }:
            return task
        time.sleep(0.5)
    raise RuntimeError(f"A2A task {task_id} did not finish within 45 seconds")


def main() -> int:
    api_url = os.getenv("RUNSIGIL_API_URL", "http://localhost:8000").rstrip("/")
    gateway_url = os.getenv("RUNSIGIL_GATEWAY_URL", "http://localhost:8080").rstrip("/")
    api_key = os.getenv("RUNSIGIL_API_KEY", "")
    if len(api_key) < 20:
        raise RuntimeError("Set RUNSIGIL_API_KEY to the local bootstrap API key before running")

    with httpx.Client(base_url=api_url, timeout=10.0) as api:
        context = _json(api.get("/v1/context", headers={"Authorization": f"Bearer {api_key}"}))
    for collection in ("projects", "environments", "agents"):
        if not context.get(collection):
            raise RuntimeError(f"Seeded context has no {collection}")

    mcp_recipient = f"mcp-live-{uuid4().hex[:8]}@example.test"
    a2a_recipient = f"a2a-live-{uuid4().hex[:8]}@example.test"
    cancel_recipient = f"cancel-live-{uuid4().hex[:8]}@example.test"
    with httpx.Client(base_url=gateway_url, timeout=10.0) as gateway:
        discovery = _mcp(gateway, api_key, "server/discover", {}, 1)
        if discovery.get("supportedVersions") != [MCP_VERSION]:
            raise RuntimeError("MCP discovery did not select the released protocol version")

        mcp_started = _mcp(
            gateway,
            api_key,
            "tools/call",
            {"name": MCP_TOOL, "arguments": _arguments(context, "mcp-live", mcp_recipient)},
            2,
            name=MCP_TOOL,
        )
        mcp_task_id = str(mcp_started["taskId"])
        mcp_waiting = _mcp(gateway, api_key, "tasks/get", {"taskId": mcp_task_id}, 3)
        digest = mcp_waiting["inputRequests"]["approval"]["params"]["requestedSchema"][
            "properties"
        ]["content_digest"]["const"]
        if mcp_waiting.get("status") != "input_required" or mcp_recipient in json.dumps(
            mcp_waiting
        ):
            raise RuntimeError("MCP approval task was not correctly redacted")
        _mcp(
            gateway,
            api_key,
            "tasks/update",
            {
                "taskId": mcp_task_id,
                "inputResponses": {
                    "approval": {
                        "action": "accept",
                        "content": {
                            "content_digest": digest,
                            "decision": "approve",
                            "reason": "Approved by the MCP live proof",
                        },
                    }
                },
            },
            4,
        )
        mcp_completed = _poll_mcp(gateway, api_key, mcp_task_id)
        if mcp_completed.get("status") != "completed":
            raise RuntimeError("MCP task did not complete")

        a2a_started = _a2a(
            gateway,
            api_key,
            "SendMessage",
            {
                "message": {
                    "messageId": str(uuid4()),
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "data": {
                                "operation": "runsigil.governed_action.start",
                                **_arguments(context, "a2a-live", a2a_recipient),
                            }
                        }
                    ],
                },
                "configuration": {"returnImmediately": True},
            },
            101,
        )["task"]
        a2a_task_id = str(a2a_started["id"])
        approval_data = a2a_started["status"]["message"]["parts"][0]["data"]
        if a2a_started["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
            raise RuntimeError("A2A task did not stop at the approval boundary")
        _a2a(
            gateway,
            api_key,
            "SendMessage",
            {
                "message": {
                    "messageId": str(uuid4()),
                    "taskId": a2a_task_id,
                    "contextId": a2a_task_id,
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "data": {
                                "operation": "runsigil.approval.decision",
                                "content_digest": approval_data["contentDigest"],
                                "decision": "approve",
                                "reason": "Approved by the A2A live proof",
                            }
                        }
                    ],
                },
                "configuration": {"returnImmediately": True},
            },
            102,
        )
        a2a_completed = _poll_a2a(gateway, api_key, a2a_task_id)
        if a2a_completed["status"]["state"] != "TASK_STATE_COMPLETED":
            raise RuntimeError("A2A task did not complete")
        if a2a_recipient in json.dumps(a2a_completed):
            raise RuntimeError("A2A task disclosed the raw recipient")

        cancel_started = _a2a(
            gateway,
            api_key,
            "SendMessage",
            {
                "message": {
                    "messageId": str(uuid4()),
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "data": {
                                "operation": "runsigil.governed_action.start",
                                **_arguments(context, "a2a-cancel", cancel_recipient),
                            }
                        }
                    ],
                },
                "configuration": {"returnImmediately": True},
            },
            103,
        )["task"]
        cancelled = _a2a(
            gateway,
            api_key,
            "CancelTask",
            {"id": cancel_started["id"]},
            104,
        )
        if cancelled["status"]["state"] != "TASK_STATE_CANCELED":
            raise RuntimeError("A2A cancellation did not remain pre-effect")

    print(
        json.dumps(
            {
                "status": "passed",
                "mcp": {
                    "protocolVersion": MCP_VERSION,
                    "taskId": mcp_task_id,
                    "taskStatus": mcp_completed["status"],
                },
                "a2a": {
                    "protocolVersion": "1.0",
                    "taskId": a2a_task_id,
                    "taskStatus": a2a_completed["status"]["state"],
                    "cancelledTaskId": cancel_started["id"],
                },
                "rawRecipientsDisclosed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (httpx.HTTPError, RuntimeError, KeyError, TypeError) as error:
        print(f"protocol live proof failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
