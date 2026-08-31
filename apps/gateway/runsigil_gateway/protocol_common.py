from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Request
from runsigil_contracts.errors import ErrorCode, RunSigilError

from runsigil_gateway.control_plane import (
    HttpProtocolControlPlane,
    JsonObject,
    ProtocolControlPlane,
)
from runsigil_gateway.settings import GatewaySettings, get_gateway_settings


def get_protocol_control_plane(request: Request) -> ProtocolControlPlane:
    override = getattr(request.app.state, "protocol_control_plane", None)
    if override is not None:
        return override  # type: ignore[no-any-return]
    return HttpProtocolControlPlane(get_gateway_settings())


def require_bearer(request: Request) -> str:
    value = request.headers.get("Authorization")
    if value is None:
        raise RunSigilError(
            ErrorCode.AUTH_REQUIRED, "Bearer authentication is required.", status_code=401
        )
    scheme, separator, credential = value.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not credential.strip():
        raise RunSigilError(
            ErrorCode.AUTH_INVALID, "Bearer authentication is invalid.", status_code=401
        )
    return f"Bearer {credential.strip()}"


def origin_allowed(request: Request, settings: GatewaySettings) -> bool:
    origin = request.headers.get("Origin")
    return origin is None or origin.rstrip("/") in settings.allowed_protocol_origins


def parse_uuid(value: Any, *, field: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a UUID string")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a UUID string") from exc


def approval_preview(run: JsonObject) -> JsonObject | None:
    approval = run.get("approval")
    if not isinstance(approval, dict):
        return None
    raw_preview = approval.get("request_preview")
    preview = raw_preview if isinstance(raw_preview, dict) else {}
    return {
        "approvalId": approval.get("id"),
        "contentDigest": approval.get("content_digest"),
        "risk": approval.get("risk"),
        "expiresAt": approval.get("expires_at"),
        "requestPreview": {
            key: preview[key]
            for key in ("tool", "recipient", "amount_cents", "binding")
            if key in preview
        },
    }


def safe_run_result(run: JsonObject) -> JsonObject:
    action = run.get("action")
    action_result: JsonObject | None = None
    if isinstance(action, dict):
        action_result = {
            "id": action.get("id"),
            "toolName": action.get("tool_name"),
            "state": action.get("state"),
            "receiptPreview": action.get("receipt_preview"),
            "errorCode": action.get("error_code"),
        }
    return {
        "runId": run.get("id"),
        "status": run.get("status"),
        "inputDigest": run.get("input_digest"),
        "action": action_result,
        "evidenceStatus": run.get("evidence_status"),
    }


def run_was_rejected(run: JsonObject) -> bool:
    traces = run.get("trace_events")
    return isinstance(traces, list) and any(
        isinstance(event, dict) and event.get("event_type") == "approval.denied" for event in traces
    )
