from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from runsigil_contracts import (
    ContentBoundDecisionArguments,
    GovernedActionArguments,
    canonical_digest,
)
from runsigil_contracts.errors import RunSigilError

from runsigil_gateway.control_plane import ControlPlaneError, JsonObject, ProtocolControlPlane
from runsigil_gateway.protocol_common import (
    approval_preview,
    get_protocol_control_plane,
    origin_allowed,
    parse_uuid,
    require_bearer,
    run_was_rejected,
    safe_run_result,
)
from runsigil_gateway.settings import get_gateway_settings

router = APIRouter()

A2A_VERSION = "1.0"
START_OPERATION = "runsigil.governed_action.start"
DECISION_OPERATION = "runsigil.approval.decision"


@dataclass(frozen=True)
class A2AFault(Exception):
    code: int
    message: str
    status_code: int = 200
    reason: str | None = None
    metadata: JsonObject | None = None


def _a2a_error(request_id: Any, fault: A2AFault) -> JSONResponse:
    error: JsonObject = {"code": fault.code, "message": fault.message}
    if fault.reason is not None:
        detail: JsonObject = {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": fault.reason,
            "domain": "a2a-protocol.org",
        }
        if fault.metadata:
            detail["metadata"] = fault.metadata
        error["data"] = [detail]
    return JSONResponse(
        status_code=fault.status_code,
        content={"jsonrpc": "2.0", "id": request_id, "error": error},
    )


def _a2a_response(request_id: Any, result: JsonObject) -> JSONResponse:
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": result})


def _agent_card() -> JsonObject:
    settings = get_gateway_settings()
    rpc_url = f"{settings.protocol_public_base_url.rstrip('/')}/a2a/rpc"
    return {
        "name": "RunSigil Governed Action Agent",
        "description": (
            "Starts and observes durable policy-governed actions with exact-content approvals."
        ),
        "supportedInterfaces": [
            {
                "url": rpc_url,
                "protocolBinding": "JSONRPC",
                "protocolVersion": A2A_VERSION,
            }
        ],
        "provider": {"organization": "RunSigil", "url": "https://runsigil.io"},
        "version": "0.1.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "securitySchemes": {
            "runsigilBearer": {
                "httpAuthSecurityScheme": {
                    "description": "A scoped RunSigil API key.",
                    "scheme": "Bearer",
                    "bearerFormat": "RunSigil API key",
                }
            }
        },
        "securityRequirements": [{"schemes": {"runsigilBearer": {"list": []}}}],
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "governed-action",
                "name": "Governed action",
                "description": (
                    "Durably records an action, evaluates policy and budget, requests approval "
                    "when required, and executes only after final online authorization."
                ),
                "tags": ["governance", "approval", "durable-execution"],
                "examples": ['{"operation":"runsigil.governed_action.start","amount_cents":4200}'],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
        ],
    }


@router.get("/.well-known/agent-card.json")
async def agent_card(request: Request) -> Response:
    card = _agent_card()
    etag = f'"{canonical_digest(card)}"'
    headers = {
        "Cache-Control": "public, max-age=300",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=card, headers=headers)


def _a2a_state(run: JsonObject) -> str:
    status = run.get("status")
    if status in {"authorizing", "queued"}:
        return "TASK_STATE_SUBMITTED"
    if status in {"running", "reconciliation_required"}:
        return "TASK_STATE_WORKING"
    if status == "waiting_for_approval":
        return "TASK_STATE_INPUT_REQUIRED"
    if status == "completed":
        return "TASK_STATE_COMPLETED"
    if status == "failed":
        return "TASK_STATE_FAILED"
    if status == "cancelled":
        return "TASK_STATE_REJECTED" if run_was_rejected(run) else "TASK_STATE_CANCELED"
    return "TASK_STATE_UNSPECIFIED"


def _status_message(run: JsonObject, state: str) -> JsonObject | None:
    if state != "TASK_STATE_INPUT_REQUIRED":
        return None
    approval = approval_preview(run)
    if approval is None:
        return None
    run_id = run.get("id")
    return {
        "messageId": f"runsigil-approval-{run_id}",
        "contextId": run_id,
        "taskId": run_id,
        "role": "ROLE_AGENT",
        "parts": [{"data": {"operation": "runsigil.approval.request", **approval}}],
    }


def _a2a_task(run: JsonObject, *, include_artifacts: bool = True) -> JsonObject:
    run_id = run.get("id")
    state = _a2a_state(run)
    status: JsonObject = {"state": state, "timestamp": run.get("updated_at")}
    message = _status_message(run, state)
    if message is not None:
        status["message"] = message
    task: JsonObject = {
        "id": run_id,
        "contextId": run_id,
        "status": status,
        "metadata": {
            "projectId": run.get("project_id"),
            "environmentId": run.get("environment_id"),
            "agentId": run.get("agent_id"),
            "inputDigest": run.get("input_digest"),
            "activeNode": run.get("active_node"),
            "evidenceStatus": run.get("evidence_status"),
        },
    }
    if include_artifacts and state == "TASK_STATE_COMPLETED":
        task["artifacts"] = [
            {
                "artifactId": f"runsigil-result-{run_id}",
                "name": "Governed action result",
                "parts": [{"data": safe_run_result(run)}],
            }
        ]
    return task


def _validate_message(params: JsonObject) -> tuple[JsonObject, JsonObject]:
    message = params.get("message")
    if not isinstance(message, dict):
        raise A2AFault(-32602, "Invalid parameters")
    if (
        message.get("role") != "ROLE_USER"
        or not isinstance(message.get("messageId"), str)
        or not message.get("messageId")
    ):
        raise A2AFault(-32602, "Invalid parameters")
    parts = message.get("parts")
    if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict):
        raise A2AFault(
            -32005,
            "Only one structured data part is supported.",
            reason="CONTENT_TYPE_NOT_SUPPORTED",
        )
    part = parts[0]
    present = [name for name in ("text", "raw", "url", "data") if name in part]
    if present != ["data"] or not isinstance(part["data"], dict):
        raise A2AFault(
            -32005,
            "Only structured application/json data parts are supported.",
            reason="CONTENT_TYPE_NOT_SUPPORTED",
        )
    configuration = params.get("configuration", {})
    if not isinstance(configuration, dict):
        raise A2AFault(-32602, "Invalid parameters")
    if configuration.get("taskPushNotificationConfig") is not None:
        raise A2AFault(
            -32003,
            "Push notifications are not supported.",
            reason="PUSH_NOTIFICATION_NOT_SUPPORTED",
        )
    accepted = configuration.get("acceptedOutputModes")
    if accepted is not None and (
        not isinstance(accepted, list) or "application/json" not in accepted
    ):
        raise A2AFault(
            -32005,
            "The requested output content type is not supported.",
            reason="CONTENT_TYPE_NOT_SUPPORTED",
        )
    return message, part["data"]


async def _wait_for_boundary(
    control: ProtocolControlPlane,
    authorization: str,
    run: JsonObject,
    *,
    return_immediately: bool,
) -> JsonObject:
    if return_immediately or run.get("status") in {
        "waiting_for_approval",
        "completed",
        "failed",
        "cancelled",
    }:
        return run
    deadline = time.monotonic() + get_gateway_settings().a2a_blocking_timeout_seconds
    current = run
    while time.monotonic() < deadline:
        await asyncio.sleep(0.25)
        run_id = parse_uuid(current.get("id"), field="run.id")
        current = await control.get_run(authorization, run_id)
        if current.get("status") in {
            "waiting_for_approval",
            "completed",
            "failed",
            "cancelled",
        }:
            return current
    task_id = current.get("id")
    raise A2AFault(
        -32603,
        "Task did not reach a response boundary before the server timeout.",
        reason="TASK_WAIT_TIMEOUT",
        metadata={"taskId": task_id} if isinstance(task_id, str) else None,
    )


async def _send_message(
    control: ProtocolControlPlane,
    authorization: str,
    params: JsonObject,
) -> JsonObject:
    message, data = _validate_message(params)
    configuration = params.get("configuration", {})
    return_immediately = configuration.get("returnImmediately", False)
    if not isinstance(return_immediately, bool):
        raise A2AFault(-32602, "Invalid parameters")
    operation = data.get("operation")
    task_id = message.get("taskId")
    if task_id is None:
        if message.get("contextId") is not None or operation != START_OPERATION:
            raise A2AFault(-32602, "Invalid parameters")
        action_data = {key: value for key, value in data.items() if key != "operation"}
        try:
            action = GovernedActionArguments.model_validate(action_data)
        except ValidationError as exc:
            raise A2AFault(-32602, "Invalid parameters") from exc
        run = await control.start_run(authorization, action.model_dump(mode="json"))
    else:
        try:
            run_id = parse_uuid(task_id, field="message.taskId")
        except ValueError as exc:
            raise A2AFault(-32602, "Invalid parameters") from exc
        run = await control.get_run(authorization, run_id)
        if message.get("contextId") not in {None, run.get("id")}:
            raise A2AFault(-32602, "Invalid parameters")
        if run.get("status") != "waiting_for_approval" or operation != DECISION_OPERATION:
            raise A2AFault(
                -32004,
                "The task cannot accept this message.",
                reason="UNSUPPORTED_OPERATION",
            )
        approval = run.get("approval")
        if not isinstance(approval, dict):
            raise A2AFault(-32603, "Internal error")
        decision_data = {key: value for key, value in data.items() if key != "operation"}
        try:
            decision = ContentBoundDecisionArguments.model_validate(decision_data)
            approval_id = parse_uuid(approval.get("id"), field="approval.id")
        except (ValidationError, ValueError) as exc:
            raise A2AFault(-32602, "Invalid parameters") from exc
        run = await control.decide_approval(
            authorization, approval_id, decision.model_dump(mode="json")
        )
    run = await _wait_for_boundary(
        control,
        authorization,
        run,
        return_immediately=return_immediately,
    )
    return {"task": _a2a_task(run)}


A2A_TO_RUN_STATUSES = {
    "TASK_STATE_SUBMITTED": ["authorizing", "queued"],
    "TASK_STATE_WORKING": ["running", "reconciliation_required"],
    "TASK_STATE_INPUT_REQUIRED": ["waiting_for_approval"],
    "TASK_STATE_COMPLETED": ["completed"],
    "TASK_STATE_FAILED": ["failed"],
    "TASK_STATE_CANCELED": ["cancelled"],
    "TASK_STATE_REJECTED": ["cancelled"],
    "TASK_STATE_AUTH_REQUIRED": [],
    "TASK_STATE_UNSPECIFIED": [],
}


async def _list_tasks(
    control: ProtocolControlPlane,
    authorization: str,
    params: JsonObject,
) -> JsonObject:
    page_size = params.get("pageSize", 50)
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100:
        raise A2AFault(-32602, "Invalid parameters")
    status = params.get("status")
    if status is not None and status not in A2A_TO_RUN_STATUSES:
        raise A2AFault(-32602, "Invalid parameters")
    include_artifacts = params.get("includeArtifacts", False)
    if not isinstance(include_artifacts, bool):
        raise A2AFault(-32602, "Invalid parameters")
    history_length = params.get("historyLength")
    if history_length is not None and (
        not isinstance(history_length, int)
        or isinstance(history_length, bool)
        or history_length < 0
    ):
        raise A2AFault(-32602, "Invalid parameters")
    context_id = params.get("contextId")
    if context_id is not None:
        try:
            run = await control.get_run(authorization, parse_uuid(context_id, field="contextId"))
        except ValueError as exc:
            raise A2AFault(-32602, "Invalid parameters") from exc
        task = _a2a_task(run, include_artifacts=include_artifacts)
        if status is not None and task["status"]["state"] != status:
            return {"tasks": [], "nextPageToken": "", "pageSize": 0, "totalSize": 0}
        return {"tasks": [task], "nextPageToken": "", "pageSize": 1, "totalSize": 1}

    page_token = params.get("pageToken")
    updated_after = params.get("statusTimestampAfter")
    if page_token is not None and not isinstance(page_token, str):
        raise A2AFault(-32602, "Invalid parameters")
    if updated_after is not None and not isinstance(updated_after, str):
        raise A2AFault(-32602, "Invalid parameters")
    statuses = A2A_TO_RUN_STATUSES.get(status) if isinstance(status, str) else None
    if status is not None and not statuses:
        return {"tasks": [], "nextPageToken": "", "pageSize": 0, "totalSize": 0}
    page = await control.list_runs(
        authorization,
        limit=page_size,
        cursor=page_token,
        statuses=statuses,
        updated_after=updated_after,
        terminal_kind=(
            "rejected"
            if status == "TASK_STATE_REJECTED"
            else "cancelled"
            if status == "TASK_STATE_CANCELED"
            else None
        ),
    )
    items = page.get("items")
    if not isinstance(items, list):
        raise A2AFault(-32603, "Internal error")
    tasks = [
        _a2a_task(item, include_artifacts=include_artifacts)
        for item in items
        if isinstance(item, dict)
    ]
    next_page = page.get("next_cursor")
    return {
        "tasks": tasks,
        "nextPageToken": next_page if isinstance(next_page, str) else "",
        "pageSize": len(tasks),
        "totalSize": page.get("total", len(tasks)),
    }


async def _dispatch(
    request: Request,
    authorization: str,
    method: str,
    params: JsonObject,
) -> JsonObject:
    control = get_protocol_control_plane(request)
    if method == "SendMessage":
        return await _send_message(control, authorization, params)
    if method == "GetTask":
        try:
            run_id = parse_uuid(params.get("id"), field="id")
        except ValueError as exc:
            raise A2AFault(-32602, "Invalid parameters") from exc
        return _a2a_task(await control.get_run(authorization, run_id))
    if method == "ListTasks":
        return await _list_tasks(control, authorization, params)
    if method == "CancelTask":
        try:
            run_id = parse_uuid(params.get("id"), field="id")
        except ValueError as exc:
            raise A2AFault(-32602, "Invalid parameters") from exc
        return _a2a_task(await control.cancel_run(authorization, run_id))
    if method in {
        "SendStreamingMessage",
        "SubscribeToTask",
        "CreateTaskPushNotificationConfig",
        "GetTaskPushNotificationConfig",
        "ListTaskPushNotificationConfigs",
        "DeleteTaskPushNotificationConfig",
        "GetExtendedAgentCard",
    }:
        raise A2AFault(
            -32004,
            "The requested optional operation is not supported.",
            reason="UNSUPPORTED_OPERATION",
        )
    raise A2AFault(-32601, "Method not found")


@router.post("/a2a/rpc")
async def a2a_rpc(request: Request) -> JSONResponse:
    request_id: Any = None
    settings = get_gateway_settings()
    if not origin_allowed(request, settings):
        return _a2a_error(None, A2AFault(-32600, "Forbidden Origin", 403))
    content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return _a2a_error(None, A2AFault(-32600, "Request payload validation error", 400))
    try:
        body = await request.json()
    except ValueError:
        return _a2a_error(None, A2AFault(-32700, "Invalid JSON payload", 400))
    if isinstance(body, dict):
        request_id = body.get("id")
    if (
        not isinstance(body, dict)
        or body.get("jsonrpc") != "2.0"
        or "id" not in body
        or not isinstance(body.get("method"), str)
        or not isinstance(body.get("params", {}), dict)
    ):
        return _a2a_error(request_id, A2AFault(-32600, "Request payload validation error"))
    requested_version = request.headers.get("A2A-Version", "0.3")
    if requested_version != A2A_VERSION:
        return _a2a_error(
            request_id,
            A2AFault(
                -32009,
                "Protocol version not supported.",
                reason="VERSION_NOT_SUPPORTED",
                metadata={
                    "requestedVersion": requested_version,
                    "supportedVersions": [A2A_VERSION],
                },
            ),
        )
    try:
        authorization = require_bearer(request)
        result = await _dispatch(
            request,
            authorization,
            body["method"],
            body.get("params", {}),
        )
        return _a2a_response(request_id, result)
    except A2AFault as exc:
        return _a2a_error(request_id, exc)
    except RunSigilError as exc:
        response = _a2a_error(
            request_id,
            A2AFault(
                -32603,
                exc.message,
                exc.status_code,
                reason=exc.code.value.removeprefix("RUNSIGIL_"),
            ),
        )
        if exc.status_code == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response
    except ControlPlaneError as exc:
        if exc.status_code == 404:
            fault = A2AFault(-32001, "Task not found", reason="TASK_NOT_FOUND")
        elif exc.status_code == 409 and exc.code == "RUNSIGIL_INVALID_TRANSITION":
            fault = A2AFault(
                -32002,
                "Task not cancelable",
                reason="TASK_NOT_CANCELABLE",
            )
        else:
            fault = A2AFault(
                -32603,
                exc.message,
                exc.status_code,
                reason=exc.code.removeprefix("RUNSIGIL_"),
            )
        response = _a2a_error(request_id, fault)
        if exc.status_code == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response
