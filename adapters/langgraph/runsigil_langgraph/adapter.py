from __future__ import annotations

from typing import Any, TypedDict

from langgraph.types import interrupt
from runsigil_contracts import ContentBoundDecisionArguments, GovernedActionArguments
from runsigil_sdk import AdapterManifest, AdapterSettings, RunSigilClient, safe_run_result


class RunSigilGraphState(TypedDict, total=False):
    runsigil_action: dict[str, Any]
    runsigil_run: dict[str, Any]


class LangGraphRunSigilAdapter:
    def __init__(self, client: RunSigilClient, settings: AdapterSettings) -> None:
        self.client = client
        self.settings = settings

    @staticmethod
    def manifest() -> AdapterManifest:
        return AdapterManifest(
            framework="langgraph",
            framework_version="1.2.11",
            native_interruptions=True,
        )

    def node(self, state: RunSigilGraphState) -> RunSigilGraphState:
        supplied = state.get("runsigil_action")
        if not isinstance(supplied, dict):
            raise ValueError("runsigil_action must be a governed-action object")
        request = GovernedActionArguments.model_validate(
            {
                "project_id": self.settings.project_id,
                "environment_id": self.settings.environment_id,
                "agent_id": self.settings.agent_id,
                **supplied,
            }
        )
        run = self.client.start_action(request)
        if run.get("status") == "waiting_for_approval":
            approval = run.get("approval")
            if not isinstance(approval, dict):
                raise RuntimeError("RunSigil omitted the exact-content approval")
            response = interrupt(
                {
                    "type": "runsigil.exact_content_approval/v1",
                    "run_id": run.get("id"),
                    "approval_id": approval.get("id"),
                    "content_digest": approval.get("content_digest"),
                    "risk": approval.get("risk"),
                    "reason": approval.get("reason"),
                    "request_preview": approval.get("request_preview"),
                    "expires_at": approval.get("expires_at"),
                    "raw_content_captured": False,
                }
            )
            if not isinstance(response, dict):
                raise ValueError("the approval resume value must be an object")
            decision = ContentBoundDecisionArguments.model_validate(response)
            if decision.content_digest != approval.get("content_digest"):
                raise ValueError("the resumed approval digest does not match the exact content")
            run = self.client.decide_approval(str(approval["id"]), decision)
        run = self.client.wait_for_terminal(run)
        return {"runsigil_run": safe_run_result(run)}
