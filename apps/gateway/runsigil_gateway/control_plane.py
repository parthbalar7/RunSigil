from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import httpx

from runsigil_gateway.settings import GatewaySettings

JsonObject = dict[str, Any]


class ControlPlaneError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class ProtocolControlPlane(Protocol):
    async def authenticate(self, authorization: str) -> None: ...

    async def start_run(self, authorization: str, arguments: JsonObject) -> JsonObject: ...

    async def get_run(self, authorization: str, run_id: UUID) -> JsonObject: ...

    async def list_runs(
        self,
        authorization: str,
        *,
        limit: int,
        cursor: str | None,
        statuses: list[str] | None,
        updated_after: str | None,
        terminal_kind: str | None,
    ) -> JsonObject: ...

    async def decide_approval(
        self,
        authorization: str,
        approval_id: UUID,
        decision: JsonObject,
    ) -> JsonObject: ...

    async def cancel_run(self, authorization: str, run_id: UUID) -> JsonObject: ...


class HttpProtocolControlPlane:
    def __init__(self, settings: GatewaySettings) -> None:
        self._base_url = settings.control_api_url.rstrip("/")
        self._timeout = settings.gateway_request_timeout_seconds
        self._max_bytes = settings.protocol_control_response_max_bytes

    async def _request(
        self,
        method: str,
        path: str,
        authorization: str,
        *,
        json: JsonObject | None = None,
        params: list[tuple[str, str | int | float | bool | None]] | None = None,
    ) -> JsonObject:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.request(
                    method,
                    f"{self._base_url}{path}",
                    headers={"Authorization": authorization, "Accept": "application/json"},
                    json=json,
                    params=params,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ControlPlaneError(
                503,
                "RUNSIGIL_INTERNAL_DEPENDENCY_UNAVAILABLE",
                "The RunSigil control plane is unavailable.",
            ) from exc
        if len(response.content) > self._max_bytes:
            raise ControlPlaneError(
                502,
                "RUNSIGIL_INTERNAL_DEPENDENCY_UNAVAILABLE",
                "The RunSigil control plane response exceeded the configured limit.",
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ControlPlaneError(
                502,
                "RUNSIGIL_INTERNAL_DEPENDENCY_UNAVAILABLE",
                "The RunSigil control plane returned an invalid response.",
            ) from exc
        if response.is_error:
            code = body.get("code") if isinstance(body, dict) else None
            message = body.get("message") if isinstance(body, dict) else None
            raise ControlPlaneError(
                response.status_code,
                code if isinstance(code, str) else "RUNSIGIL_INTERNAL_DEPENDENCY_UNAVAILABLE",
                message
                if isinstance(message, str)
                else "The RunSigil control plane denied the request.",
            )
        if not isinstance(body, dict):
            raise ControlPlaneError(
                502,
                "RUNSIGIL_INTERNAL_DEPENDENCY_UNAVAILABLE",
                "The RunSigil control plane response is not an object.",
            )
        return body

    async def authenticate(self, authorization: str) -> None:
        await self._request("GET", "/v1/context", authorization)

    async def start_run(self, authorization: str, arguments: JsonObject) -> JsonObject:
        return await self._request("POST", "/v1/runs", authorization, json=arguments)

    async def get_run(self, authorization: str, run_id: UUID) -> JsonObject:
        return await self._request("GET", f"/v1/runs/{run_id}", authorization)

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
        params: list[tuple[str, str | int | float | bool | None]] = [("limit", limit)]
        if cursor is not None:
            params.append(("cursor", cursor))
        if updated_after is not None:
            params.append(("updated_after", updated_after))
        if terminal_kind is not None:
            params.append(("terminal_kind", terminal_kind))
        params.extend(("status", value) for value in statuses or [])
        return await self._request("GET", "/v1/runs", authorization, params=params)

    async def decide_approval(
        self,
        authorization: str,
        approval_id: UUID,
        decision: JsonObject,
    ) -> JsonObject:
        return await self._request(
            "POST",
            f"/v1/approvals/{approval_id}/decision",
            authorization,
            json=decision,
        )

    async def cancel_run(self, authorization: str, run_id: UUID) -> JsonObject:
        return await self._request("POST", f"/v1/runs/{run_id}/cancel", authorization)
