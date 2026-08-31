from __future__ import annotations

import hmac
from typing import Annotated, Any, cast
from urllib.parse import quote

import httpx
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from runsigil_contracts import ActionExecutionRequest, ActionExecutionResult, canonical_digest
from runsigil_contracts.errors import ErrorCode, RunSigilError
from runsigil_telemetry import Operation, TelemetryConfig, configure_telemetry

from runsigil_gateway.a2a import router as a2a_router
from runsigil_gateway.egress import validate_fixed_destination
from runsigil_gateway.mcp import router as mcp_router
from runsigil_gateway.settings import GatewaySettings, get_gateway_settings
from runsigil_gateway.tokens import mint_audience_token

_settings = get_gateway_settings()
configure_telemetry(
    TelemetryConfig(
        service_name="runsigil-gateway",
        enabled=_settings.otel_enabled,
        otlp_http_endpoint=_settings.otel_exporter_otlp_endpoint,
    )
)

app = FastAPI(title="RunSigil Protocol and Egress Gateway", version="0.2.0")
app.include_router(mcp_router)
app.include_router(a2a_router)


def _verify_worker(token: str | None, settings: GatewaySettings) -> None:
    if token is None or not hmac.compare_digest(token, settings.internal_service_token):
        raise RunSigilError(
            ErrorCode.AUTH_INVALID, "Invalid worker service credential.", status_code=401
        )


async def _authorize(
    request: ActionExecutionRequest, mode: str, settings: GatewaySettings
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
        response = await client.post(
            f"{settings.control_api_url.rstrip('/')}/internal/v1/actions/{request.action_id}/authorize",
            headers={"X-RunSigil-Service-Token": settings.gateway_service_token},
            json={
                "content_digest": request.content_digest,
                "claim_token": request.claim_token,
                "mode": mode,
            },
        )
    if response.status_code != 200:
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "Final online authorization denied provider egress.",
            status_code=403,
        )
    raw_authorization: Any = response.json()
    if not isinstance(raw_authorization, dict):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "Authorization response is not an object.",
            status_code=403,
        )
    authorization = cast(dict[str, Any], raw_authorization)
    if authorization.get("content_digest") != request.content_digest:
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED, "Authorization digest mismatch.", status_code=403
        )
    if authorization.get("arguments_digest") != canonical_digest(request.arguments):
        raise RunSigilError(
            ErrorCode.ACTION_NOT_AUTHORIZED,
            "Action arguments do not match durable intent.",
            status_code=403,
        )
    return authorization


def _provider_headers(
    request: ActionExecutionRequest,
    authorization: dict[str, Any],
    settings: GatewaySettings,
) -> dict[str, str]:
    token = mint_audience_token(
        signing_key=settings.demo_provider_signing_key,
        audience=authorization["audience"],
        subject=authorization["workload_subject"],
        action_id=str(request.action_id),
        run_id=str(request.run_id),
        content_digest=request.content_digest,
    )
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": request.idempotency_key,
        "Content-Type": "application/json",
    }


@app.exception_handler(RunSigilError)
async def handle_runsigil_error(_request: Request, exc: RunSigilError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"code": exc.code.value, "message": exc.message}
    )


@app.post("/v1/actions/execute", response_model=ActionExecutionResult)
async def execute_action(
    request: ActionExecutionRequest,
    service_token: Annotated[str | None, Header(alias="X-RunSigil-Service-Token")] = None,
) -> ActionExecutionResult:
    settings = get_gateway_settings()
    _verify_worker(service_token, settings)
    validate_fixed_destination(
        settings.demo_provider_url,
        allow_private=settings.allow_private_demo_provider,
        production=settings.environment.lower() in {"production", "prod"},
    )
    authorization = await _authorize(request, "execute", settings)
    try:
        with Operation(
            "execute_tool demo.invoice.send",
            metric_name="gen_ai.execute_tool.duration",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "demo.invoice.send",
                "runsigil.run.id": str(request.run_id),
                "runsigil.action.id": str(request.action_id),
                "runsigil.content_captured": False,
            },
        ):
            async with httpx.AsyncClient(
                timeout=settings.gateway_request_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.post(
                    settings.demo_provider_url,
                    headers=_provider_headers(request, authorization, settings),
                    json=request.arguments,
                )
        if len(response.content) > settings.gateway_response_max_bytes:
            return ActionExecutionResult(
                outcome="ambiguous", error_code="provider_response_too_large"
            )
        if response.status_code >= 500:
            return ActionExecutionResult(outcome="ambiguous", error_code="provider_server_error")
        if response.status_code >= 400:
            return ActionExecutionResult(outcome="failed", error_code="provider_rejected")
        body = response.json()
        return ActionExecutionResult(
            outcome="committed",
            provider_reference=body.get("effect_id"),
            receipt_preview={
                "status": body.get("status", "committed"),
                "effect_id": body.get("effect_id"),
                "credential_audience": body.get("credential_audience"),
                "credential_subject": body.get("credential_subject"),
            },
        )
    except (httpx.TimeoutException, httpx.NetworkError, ValueError):
        return ActionExecutionResult(outcome="ambiguous", error_code="provider_outcome_unknown")


@app.post("/v1/actions/reconcile", response_model=ActionExecutionResult)
async def reconcile_action(
    request: ActionExecutionRequest,
    service_token: Annotated[str | None, Header(alias="X-RunSigil-Service-Token")] = None,
) -> ActionExecutionResult:
    settings = get_gateway_settings()
    _verify_worker(service_token, settings)
    validate_fixed_destination(
        settings.demo_provider_url,
        allow_private=settings.allow_private_demo_provider,
        production=settings.environment.lower() in {"production", "prod"},
    )
    authorization = await _authorize(request, "reconcile", settings)
    url = f"{settings.demo_provider_url.rstrip('/')}/{quote(request.idempotency_key, safe='')}"
    try:
        async with httpx.AsyncClient(
            timeout=settings.gateway_request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(
                url, headers=_provider_headers(request, authorization, settings)
            )
        if len(response.content) > settings.gateway_response_max_bytes:
            return ActionExecutionResult(
                outcome="ambiguous", error_code="provider_response_too_large"
            )
        if response.status_code == 404:
            return ActionExecutionResult(outcome="failed", error_code="provider_confirmed_absent")
        if response.status_code != 200:
            return ActionExecutionResult(
                outcome="ambiguous", error_code="provider_reconciliation_unknown"
            )
        body = response.json()
        return ActionExecutionResult(
            outcome="committed",
            provider_reference=body.get("effect_id"),
            receipt_preview={
                "status": "committed",
                "effect_id": body.get("effect_id"),
                "reconciled": True,
                "credential_audience": body.get("credential_audience"),
                "credential_subject": body.get("credential_subject"),
            },
        )
    except (httpx.TimeoutException, httpx.NetworkError, ValueError):
        return ActionExecutionResult(
            outcome="ambiguous", error_code="provider_reconciliation_unknown"
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "runsigil-gateway"}


@app.get("/ready")
def ready() -> dict[str, str]:
    settings = get_gateway_settings()
    validate_fixed_destination(
        settings.demo_provider_url,
        allow_private=settings.allow_private_demo_provider,
        production=settings.environment.lower() in {"production", "prod"},
    )
    return {"status": "ready", "egress_policy": "fixed-destination"}


def run() -> None:
    get_gateway_settings()
    uvicorn.run("runsigil_gateway.main:app", host="0.0.0.0", port=8080)  # noqa: S104


if __name__ == "__main__":
    run()
