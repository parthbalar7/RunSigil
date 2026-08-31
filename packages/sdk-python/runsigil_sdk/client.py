from __future__ import annotations

import time
from typing import Any

import httpx
from runsigil_contracts import ContentBoundDecisionArguments, GovernedActionArguments

from runsigil_sdk.models import AdapterSettings

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "dead_lettered"})


class RunSigilClientError(RuntimeError):
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(str(body.get("message", "RunSigil request failed")))


class RunSigilClient:
    def __init__(
        self,
        settings: AdapterSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._client = httpx.Client(
            base_url=str(settings.base_url).rstrip("/"),
            headers={"Authorization": f"Bearer {settings.api_key.get_secret_value()}"},
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RunSigilClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._client.request(method, path, **kwargs)
        try:
            body = response.json()
        except ValueError as exc:
            raise RunSigilClientError(
                response.status_code,
                {"message": "RunSigil returned a non-JSON response."},
            ) from exc
        if not isinstance(body, dict):
            raise RunSigilClientError(
                response.status_code,
                {"message": "RunSigil returned an invalid response object."},
            )
        if response.status_code >= 400:
            raise RunSigilClientError(response.status_code, body)
        return body

    def start_action(self, request: GovernedActionArguments) -> dict[str, Any]:
        return self._request("POST", "/v1/runs", json=request.model_dump(mode="json"))

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/runs/{run_id}")

    def decide_approval(
        self,
        approval_id: str,
        decision: ContentBoundDecisionArguments,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/approvals/{approval_id}/decision",
            json=decision.model_dump(mode="json"),
        )

    def wait_for_terminal(
        self,
        run: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 0.25,
    ) -> dict[str, Any]:
        timeout = (
            self.settings.terminal_wait_seconds if timeout_seconds is None else timeout_seconds
        )
        deadline = time.monotonic() + timeout
        current = run
        while current.get("status") not in TERMINAL_STATUSES and time.monotonic() < deadline:
            run_id = current.get("id")
            if not isinstance(run_id, str):
                raise RunSigilClientError(0, {"message": "RunSigil response omitted the run id."})
            time.sleep(poll_interval_seconds)
            current = self.get_run(run_id)
        return current
