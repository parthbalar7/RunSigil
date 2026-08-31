from __future__ import annotations

import asyncio
from typing import Any

from agents import FunctionTool, function_tool
from agents.tool_context import ToolContext
from runsigil_contracts import ContentBoundDecisionArguments, GovernedActionArguments
from runsigil_sdk import AdapterManifest, AdapterSettings, RunSigilClient, safe_run_result


class OpenAIAgentsRunSigilAdapter:
    def __init__(self, client: RunSigilClient, settings: AdapterSettings) -> None:
        self.client = client
        self.settings = settings

    @staticmethod
    def manifest() -> AdapterManifest:
        return AdapterManifest(
            framework="openai-agents",
            framework_version="0.22.0",
            native_interruptions=True,
        )

    def tools(self) -> list[FunctionTool]:
        settings = self.settings
        client = self.client

        @function_tool(
            name_override="runsigil_send_invoice",
            description_override=(
                "Submit an invoice notification through RunSigil governance. "
                "This side-effecting tool always pauses for framework approval."
            ),
            needs_approval=True,
        )
        async def send_invoice(
            context: ToolContext[Any],
            recipient: str,
            amount_cents: int,
            description: str,
        ) -> dict[str, Any]:
            request = GovernedActionArguments(
                project_id=settings.project_id,
                environment_id=settings.environment_id,
                agent_id=settings.agent_id,
                recipient=recipient,
                amount_cents=amount_cents,
                description=description,
                idempotency_key=f"openai-agents-{context.tool_call_id}",
            )
            run = await asyncio.to_thread(client.start_action, request)
            if run.get("status") == "waiting_for_approval":
                approval = run.get("approval")
                if not isinstance(approval, dict):
                    raise RuntimeError("RunSigil omitted the exact-content approval")
                decision = ContentBoundDecisionArguments(
                    content_digest=str(approval["content_digest"]),
                    decision="approve",
                    reason="Approved through the OpenAI Agents SDK tool interruption.",
                )
                run = await asyncio.to_thread(
                    client.decide_approval,
                    str(approval["id"]),
                    decision,
                )
            run = await asyncio.to_thread(client.wait_for_terminal, run)
            return safe_run_result(run)

        return [send_invoice]
