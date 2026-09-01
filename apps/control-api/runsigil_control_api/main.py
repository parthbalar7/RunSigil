from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from runsigil_contracts.errors import RunSigilError
from runsigil_telemetry import TelemetryConfig, configure_telemetry
from sqlalchemy import text

from runsigil_control_api.database import SessionLocal
from runsigil_control_api.routers.core import router as core_router
from runsigil_control_api.routers.internal import router as internal_router
from runsigil_control_api.routers.workflows import router as workflow_router
from runsigil_control_api.settings import get_settings

_settings = get_settings()
configure_telemetry(
    TelemetryConfig(
        service_name="runsigil-control-api",
        enabled=_settings.otel_enabled,
        otlp_http_endpoint=_settings.otel_exporter_otlp_endpoint,
    )
)

app = FastAPI(
    title="RunSigil Control API",
    version="0.2.0",
    description="Content-bound governance for agent actions.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)
app.include_router(core_router)
app.include_router(internal_router)
app.include_router(workflow_router)


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID") or secrets.token_hex(12)
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(RunSigilError)
async def runsigil_error_handler(request: Request, exc: RunSigilError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code.value,
            "message": exc.message,
            "details": exc.details,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "runsigil-control-api"}


@app.get("/ready", response_model=None)
def ready() -> JSONResponse | dict[str, str]:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ready", "database": "available"}


def run() -> None:
    get_settings()
    uvicorn.run("runsigil_control_api.main:app", host="0.0.0.0", port=8000)  # noqa: S104


if __name__ == "__main__":
    run()
