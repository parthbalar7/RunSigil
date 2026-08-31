from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from runsigil_evidence import verify


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    api_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = client.request(method, path, headers=headers, json=payload)
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"{method} {path} returned non-JSON HTTP {response.status_code}"
        ) from exc
    if not response.is_success:
        raise RuntimeError(
            f"{method} {path} failed with HTTP {response.status_code}: "
            f"{json.dumps(body, sort_keys=True)}"
        )
    if not isinstance(body, dict):
        raise RuntimeError(f"{method} {path} returned an unexpected response shape")
    return body


def main() -> int:
    base_url = os.getenv("RUNSIGIL_API_URL", "http://localhost:8000").rstrip("/")
    api_key = os.getenv("RUNSIGIL_API_KEY", "")
    if len(api_key) < 20:
        raise RuntimeError("Set RUNSIGIL_API_KEY to the local bootstrap API key before running")

    recipient = f"live-{uuid4().hex[:8]}@example.test"
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        ready = _request(client, "GET", "/ready")
        if ready.get("status") != "ready":
            raise RuntimeError("RunSigil is not ready")

        context = _request(client, "GET", "/v1/context", api_key=api_key)
        for collection in ("projects", "environments", "agents"):
            if not context.get(collection):
                raise RuntimeError(f"The seeded context has no {collection}")

        run = _request(
            client,
            "POST",
            "/v1/runs",
            api_key=api_key,
            payload={
                "project_id": context["projects"][0]["id"],
                "environment_id": context["environments"][0]["id"],
                "agent_id": context["agents"][0]["id"],
                "recipient": recipient,
                "amount_cents": 4200,
                "description": "Live governed invoice notification",
                "idempotency_key": f"live-{uuid4()}",
                "simulate_outcome": "committed",
            },
        )
        if run.get("status") != "waiting_for_approval":
            raise RuntimeError(f"Expected waiting_for_approval, received {run.get('status')}")
        approval = run.get("approval")
        if not isinstance(approval, dict) or approval.get("status") != "pending":
            raise RuntimeError("The exact-content approval was not created")
        if recipient in json.dumps(run):
            raise RuntimeError("The API response leaked the raw recipient")

        run = _request(
            client,
            "POST",
            f"/v1/approvals/{approval['id']}/decision",
            api_key=api_key,
            payload={
                "content_digest": approval["content_digest"],
                "decision": "approve",
                "reason": "Approved by the automated local live proof",
            },
        )
        run_id = str(run["id"])
        deadline = time.monotonic() + 45
        while run.get("status") not in {"completed", "failed", "reconciliation_required"}:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Run {run_id} did not complete within 45 seconds")
            time.sleep(0.5)
            run = _request(client, "GET", f"/v1/runs/{run_id}", api_key=api_key)

        if run.get("status") != "completed":
            raise RuntimeError(
                f"Run {run_id} ended in {run.get('status')}: {run.get('error_code')}"
            )
        action = run.get("action")
        if not isinstance(action, dict) or action.get("state") != "committed":
            raise RuntimeError("The provider receipt was not durably committed")
        if recipient in json.dumps(run):
            raise RuntimeError("The completed run leaked the raw recipient")

        evidence = _request(client, "GET", f"/v1/runs/{run_id}/evidence", api_key=api_key)
        verification = verify(evidence)
        if not verification.valid:
            raise RuntimeError(verification.message)

    output_dir = Path(".runsigil")
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"live-evidence-{run_id}.json"
    output_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "passed",
                "run_id": run_id,
                "run_status": run["status"],
                "action_state": action["state"],
                "approval_digest": approval["content_digest"],
                "trace_event_count": len(run["trace_events"]),
                "evidence_digest": evidence["content_digest"],
                "signature_valid": verification.signature_valid,
                "external_trust_root_supplied": False,
                "verification_message": verification.message,
                "evidence_file": str(output_path),
                "raw_recipient_disclosed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (httpx.HTTPError, RuntimeError, KeyError) as error:
        print(f"live proof failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
