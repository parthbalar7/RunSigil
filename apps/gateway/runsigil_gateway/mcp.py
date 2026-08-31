from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from runsigil_contracts import ContentBoundDecisionArguments, GovernedActionArguments
from runsigil_contracts.errors import RunSigilError

from runsigil_gateway.control_plane import ControlPlaneError, JsonObject
from runsigil_gateway.protocol_common import (
    approval_preview,
    get_protocol_control_plane,
    origin_allowed,
    parse_uuid,
    require_bearer,
    safe_run_result,
)
from runsigil_gateway.settings import get_gateway_settings

router = APIRouter()

MCP_VERSION = "2026-07-28"
TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
TOOL_NAME = "runsigil.governed_action.start"
SUPPORTED_METHODS = {
    "server/discover",
    "tools/list",
    "tools/call",
    "tasks/get",
    "tasks/update",
    "tasks/cancel",
}


@dataclass(frozen=True)
class MCPFault(Exception):
    code: int
    message: str
    status_code: int = 200
    data: JsonObject | None = None


def _response(request_id: Any, *, result: JsonObject) -> JSONResponse:
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, fault: MCPFault) -> JSONResponse:
    error: JsonObject = {"code": fault.code, "message": fault.message}
    if fault.data is not None:
        error["data"] = fault.data
    return JSONResponse(
        status_code=fault.status_code,
        content={"jsonrpc": "2.0", "id": request_id, "error": error},
    )


def _decode_header_value(value: str) -> str:
    if value.startswith("=?base64?") and value.endswith("?="):
        encoded = value[len("=?base64?") : -2]
        try:
            return base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise MCPFault(-32020, "Header mismatch: invalid encoded header.", 400) from exc
    return value


def _validate_envelope(request: Request, body: Any) -> tuple[Any, str, JsonObject, JsonObject]:
    request_id = body.get("id") if isinstance(body, dict) else None
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "id" not in body:
        raise MCPFault(-32600, "Invalid Request", 400)
    method = body.get("method")
    params = body.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        raise MCPFault(-32600, "Invalid Request", 400)

    header_method = request.headers.get("Mcp-Method")
    header_version = request.headers.get("MCP-Protocol-Version")
    if header_method is None or header_version is None or header_method != method:
        raise MCPFault(-32020, "Header mismatch: required MCP headers do not match.", 400)

    meta = params.get("_meta")
    if not isinstance(meta, dict):
        raise MCPFault(-32602, "Invalid params: required request metadata is missing.", 400)
    body_version = meta.get("io.modelcontextprotocol/protocolVersion")
    if not isinstance(body_version, str) or header_version != body_version:
        raise MCPFault(-32020, "Header mismatch: protocol versions do not match.", 400)
    if body_version != MCP_VERSION:
        raise MCPFault(
            -32022,
            "Unsupported protocol version",
            400,
            {"supported": [MCP_VERSION], "requested": body_version},
        )
    client_info = meta.get("io.modelcontextprotocol/clientInfo")
    capabilities = meta.get("io.modelcontextprotocol/clientCapabilities")
    if (
        not isinstance(client_info, dict)
        or not isinstance(client_info.get("name"), str)
        or not client_info.get("name")
        or not isinstance(client_info.get("version"), str)
        or not client_info.get("version")
        or not isinstance(capabilities, dict)
    ):
        raise MCPFault(-32602, "Invalid params: client metadata is incomplete.", 400)

    expected_name = params.get("name") if method == "tools/call" else None
    header_name = request.headers.get("Mcp-Name")
    if expected_name is not None:
        if not isinstance(expected_name, str) or header_name is None:
            raise MCPFault(-32020, "Header mismatch: Mcp-Name is required.", 400)
        if _decode_header_value(header_name) != expected_name:
            raise MCPFault(-32020, "Header mismatch: Mcp-Name does not match.", 400)
    elif header_name is not None:
        raise MCPFault(-32020, "Header mismatch: Mcp-Name is not valid for this method.", 400)

    if method not in SUPPORTED_METHODS:
        raise MCPFault(-32601, "Method not found", 404)
    return request_id, method, params, meta


def _requires_tasks(meta: JsonObject) -> None:
    capabilities = meta.get("io.modelcontextprotocol/clientCapabilities")
    extensions = capabilities.get("extensions") if isinstance(capabilities, dict) else None
    if not isinstance(extensions, dict) or not isinstance(extensions.get(TASKS_EXTENSION), dict):
        raise MCPFault(
            -32602,
            "Invalid params: this operation requires the MCP Tasks extension.",
        )


def _tool_definition() -> JsonObject:
    return {
        "name": TOOL_NAME,
        "title": "Start a governed action",
        "description": (
            "Durably records and governs a demo invoice action. The action may require "
            "an exact-content human approval before any external effect."
        ),
        "inputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "project_id": {"type": "string", "format": "uuid"},
                "environment_id": {"type": "string", "format": "uuid"},
                "agent_id": {"type": "string", "format": "uuid"},
                "recipient": {"type": "string", "format": "email", "maxLength": 320},
                "amount_cents": {"type": "integer", "minimum": 1, "maximum": 100000},
                "description": {"type": "string", "minLength": 1, "maxLength": 200},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                "simulate_outcome": {
                    "type": "string",
                    "enum": ["committed", "ambiguous_after_commit", "failed"],
                    "default": "committed",
                },
            },
            "required": [
                "project_id",
                "environment_id",
                "agent_id",
                "recipient",
                "amount_cents",
                "description",
                "idempotency_key",
            ],
        },
        "outputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "runId": {"type": "string"},
                "status": {"type": "string"},
                "inputDigest": {"type": "string"},
                "action": {"type": ["object", "null"]},
                "evidenceStatus": {"type": "string"},
            },
            "required": ["runId", "status", "inputDigest", "action", "evidenceStatus"],
        },
    }


def _mcp_status(run_status: Any) -> tuple[str, str]:
    mapping = {
        "authorizing": ("working", "Governance authorization is in progress."),
        "waiting_for_approval": ("input_required", "Exact-content approval is required."),
        "queued": ("working", "The governed action is queued."),
        "running": ("working", "The governed action is running."),
        "reconciliation_required": (
            "working",
            "The external outcome is ambiguous and is being reconciled.",
        ),
        "completed": ("completed", "The governed action completed."),
        "failed": ("failed", "The governed action failed."),
        "cancelled": ("cancelled", "The governed action was cancelled."),
    }
    return mapping.get(run_status, ("failed", "The governed run is in an unknown state."))


def _task_base(run: JsonObject) -> JsonObject:
    status, status_message = _mcp_status(run.get("status"))
    return {
        "taskId": run.get("id"),
        "status": status,
        "statusMessage": status_message,
        "createdAt": run.get("created_at"),
        "lastUpdatedAt": run.get("updated_at"),
        "ttlMs": None,
        "pollIntervalMs": 1000,
    }


def _task_detail(run: JsonObject) -> JsonObject:
    task = _task_base(run)
    status = task["status"]
    task["resultType"] = "complete"
    if status == "input_required":
        approval = approval_preview(run)
        if approval is None or not isinstance(approval.get("contentDigest"), str):
            task["status"] = "failed"
            task["statusMessage"] = "Approval lineage is unavailable."
            task["error"] = {"code": -32603, "message": "Governance lineage is unavailable."}
            return task
        digest = approval["contentDigest"]
        task["inputRequests"] = {
            "approval": {
                "method": "elicitation/create",
                "params": {
                    "mode": "form",
                    "message": "Approve or deny the exact governed action content.",
                    "requestedSchema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "content_digest": {"type": "string", "const": digest},
                            "decision": {"type": "string", "enum": ["approve", "deny"]},
                            "reason": {"type": "string", "minLength": 2, "maxLength": 500},
                        },
                        "required": ["content_digest", "decision", "reason"],
                    },
                },
            }
        }
    elif status == "completed":
        structured = safe_run_result(run)
        task["result"] = {
            "resultType": "complete",
            "content": [
                {
                    "type": "text",
                    "text": f"Governed run {run.get('id')} completed.",
                }
            ],
            "structuredContent": structured,
            "isError": False,
        }
    elif status == "failed":
        error_code = run.get("error_code")
        data = {"runsigilCode": error_code} if isinstance(error_code, str) else None
        task["error"] = {
            "code": -32000,
            "message": "The governed run failed.",
            **({"data": data} if data is not None else {}),
        }
    return task


async def _dispatch(
    request: Request,
    authorization: str,
    method: str,
    params: JsonObject,
    meta: JsonObject,
) -> JsonObject:
    control = get_protocol_control_plane(request)
    if method == "server/discover":
        await control.authenticate(authorization)
        return {
            "resultType": "complete",
            "supportedVersions": [MCP_VERSION],
            "capabilities": {
                "tools": {"listChanged": False},
                "extensions": {TASKS_EXTENSION: {}},
            },
            "_meta": {
                "io.modelcontextprotocol/serverInfo": {
                    "name": "runsigil-governance-gateway",
                    "version": "0.1.0",
                }
            },
            "instructions": (
                "Use the governed action tool for durable, policy-checked effects. "
                "Poll its task and fulfill exact-content approval input when requested."
            ),
            "ttlMs": 300000,
            "cacheScope": "private",
        }
    if method == "tools/list":
        await control.authenticate(authorization)
        return {
            "resultType": "complete",
            "tools": [_tool_definition()],
            "ttlMs": 60000,
            "cacheScope": "private",
        }
    if method == "tools/call":
        _requires_tasks(meta)
        if params.get("name") != TOOL_NAME or not isinstance(params.get("arguments"), dict):
            raise MCPFault(-32602, "Invalid params: unknown tool or invalid arguments.")
        try:
            arguments = GovernedActionArguments.model_validate(params["arguments"])
        except ValidationError as exc:
            raise MCPFault(
                -32602, "Invalid params: governed action arguments are invalid."
            ) from exc
        run = await control.start_run(authorization, arguments.model_dump(mode="json"))
        return {"resultType": "task", **_task_base(run)}

    _requires_tasks(meta)
    try:
        run_id = parse_uuid(params.get("taskId"), field="taskId")
    except ValueError as exc:
        raise MCPFault(-32602, "Invalid params: taskId must be a UUID.") from exc
    if method == "tasks/get":
        return _task_detail(await control.get_run(authorization, run_id))
    if method == "tasks/cancel":
        await control.cancel_run(authorization, run_id)
        return {"resultType": "complete"}
    if method == "tasks/update":
        input_responses = params.get("inputResponses")
        if not isinstance(input_responses, dict):
            raise MCPFault(-32602, "Invalid params: inputResponses must be an object.")
        run = await control.get_run(authorization, run_id)
        approval = run.get("approval")
        response = input_responses.get("approval")
        if run.get("status") != "waiting_for_approval" or not isinstance(approval, dict):
            return {"resultType": "complete"}
        if response is None:
            return {"resultType": "complete"}
        if not isinstance(response, dict) or not isinstance(response.get("action"), str):
            raise MCPFault(-32602, "Invalid params: approval response is invalid.")
        action = response["action"]
        if action == "accept":
            try:
                decision = ContentBoundDecisionArguments.model_validate(response.get("content"))
            except ValidationError as exc:
                raise MCPFault(-32602, "Invalid params: approval content is invalid.") from exc
            decision_document = decision.model_dump(mode="json")
        elif action in {"decline", "cancel"}:
            digest = approval.get("content_digest")
            if not isinstance(digest, str):
                raise MCPFault(-32603, "Approval lineage is unavailable.")
            decision_document = {
                "content_digest": digest,
                "decision": "deny",
                "reason": "MCP client declined the exact-content approval request.",
            }
        else:
            raise MCPFault(-32602, "Invalid params: unsupported approval response action.")
        try:
            approval_id = parse_uuid(approval.get("id"), field="approval.id")
        except ValueError as exc:
            raise MCPFault(-32603, "Approval lineage is unavailable.") from exc
        await control.decide_approval(authorization, approval_id, decision_document)
        return {"resultType": "complete"}
    raise MCPFault(-32601, "Method not found", 404)


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    request_id: Any = None
    settings = get_gateway_settings()
    if not origin_allowed(request, settings):
        return _error(None, MCPFault(-32600, "Forbidden Origin", 403))
    content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    accept = {
        value.split(";", 1)[0].strip() for value in request.headers.get("Accept", "").split(",")
    }
    if content_type != "application/json" or not {
        "application/json",
        "text/event-stream",
    }.issubset(accept):
        return _error(None, MCPFault(-32600, "Invalid transport content negotiation.", 400))
    try:
        body = await request.json()
    except ValueError:
        return _error(None, MCPFault(-32700, "Parse error", 400))
    try:
        request_id, method, params, meta = _validate_envelope(request, body)
        authorization = require_bearer(request)
        result = await _dispatch(request, authorization, method, params, meta)
        return _response(request_id, result=result)
    except MCPFault as exc:
        return _error(request_id, exc)
    except RunSigilError as exc:
        response = _error(
            request_id,
            MCPFault(-32000, exc.message, exc.status_code, {"runsigilCode": exc.code.value}),
        )
        if exc.status_code == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response
    except ControlPlaneError as exc:
        code = -32001 if exc.status_code == 404 else -32602 if exc.status_code == 409 else -32000
        response = _error(
            request_id,
            MCPFault(code, exc.message, exc.status_code, {"runsigilCode": exc.code}),
        )
        if exc.status_code == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response
