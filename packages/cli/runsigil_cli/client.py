from __future__ import annotations

import os
from typing import Any

import httpx


class ApiError(RuntimeError):
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("message", f"RunSigil API returned HTTP {status_code}"))
        self.status_code = status_code
        self.body = body


class Client:
    def __init__(self, api_url: str | None = None, api_key: str | None = None) -> None:
        self.api_url = (
            api_url or os.environ.get("RUNSIGIL_API_URL") or "http://localhost:8000"
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("RUNSIGIL_API_KEY") or ""

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = httpx.request(
                method,
                self.api_url + path,
                headers=headers,
                timeout=10.0,
                follow_redirects=False,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise ApiError(0, {"code": "RUNSIGIL_API_UNAVAILABLE", "message": str(exc)}) from exc
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {"code": "RUNSIGIL_HTTP_ERROR", "message": response.text[:500]}
            raise ApiError(response.status_code, body)
        return response.json()
