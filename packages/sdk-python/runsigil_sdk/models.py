from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr


class AdapterSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: HttpUrl
    api_key: SecretStr
    project_id: UUID
    environment_id: UUID
    agent_id: UUID
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    terminal_wait_seconds: float = Field(default=30.0, ge=0, le=300)


class AdapterManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = "runsigil.framework-adapter/v1"
    framework: str
    framework_version: str
    native_interruptions: bool
    exact_content_approval: bool = True
    reconcile_ambiguous_effects: bool = True
    raw_content_captured: bool = False


def safe_run_result(run: dict[str, Any]) -> dict[str, Any]:
    action_value = run.get("action")
    approval_value = run.get("approval")
    action: dict[str, Any] = action_value if isinstance(action_value, dict) else {}
    approval: dict[str, Any] = approval_value if isinstance(approval_value, dict) else {}
    return {
        "run_id": run.get("id"),
        "status": run.get("status"),
        "active_node": run.get("active_node"),
        "error_code": run.get("error_code"),
        "action_state": action.get("state"),
        "content_digest": action.get("content_digest"),
        "approval_id": approval.get("id"),
        "approval_status": approval.get("status"),
        "evidence_status": run.get("evidence_status"),
        "raw_content_captured": False,
    }
