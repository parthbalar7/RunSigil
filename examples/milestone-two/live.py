from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from uuid import uuid4

import httpx
from agents.tool_context import ToolContext
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from runsigil_langgraph import LangGraphRunSigilAdapter, RunSigilGraphState
from runsigil_openai_agents import OpenAIAgentsRunSigilAdapter
from runsigil_sdk import AdapterSettings, RunSigilClient, agent_invocation


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _context(api_url: str, api_key: str) -> dict[str, Any]:
    response = httpx.get(
        f"{api_url.rstrip('/')}/v1/context",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("RunSigil context was not an object")
    return value


def _assert_evidence(api_url: str, api_key: str, run_id: str) -> int:
    response = httpx.get(
        f"{api_url.rstrip('/')}/v1/runs/{run_id}/evidence",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    response.raise_for_status()
    budgets = response.json()["manifest"]["budgets"]
    if len(budgets) != 20:
        raise RuntimeError(f"expected 20 enforced reservations, received {len(budgets)}")
    if {row["resource_key"] for row in budgets} != {
        "currency:USD",
        "requests",
        "concurrent_runs",
        "tool_actions",
    }:
        raise RuntimeError("evidence budget resource coverage is incomplete")
    return len(budgets)


def _langgraph_proof(client: RunSigilClient, settings: AdapterSettings) -> dict[str, Any]:
    adapter = LangGraphRunSigilAdapter(client, settings)
    builder = StateGraph(RunSigilGraphState)
    builder.add_node("runsigil", adapter.node)
    builder.add_edge(START, "runsigil")
    builder.add_edge("runsigil", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": f"live-{uuid4()}"}}
    state: RunSigilGraphState = {
        "runsigil_action": {
            "recipient": "milestone-two@example.test",
            "amount_cents": 17,
            "description": "Milestone 2 LangGraph live proof",
            "idempotency_key": f"langgraph-live-{uuid4()}",
        }
    }
    with agent_invocation("runsigil-langgraph-live", framework="langgraph"):
        interrupted = graph.invoke(state, config)
        approval = interrupted["__interrupt__"][0].value
        completed = graph.invoke(
            Command(
                resume={
                    "content_digest": approval["content_digest"],
                    "decision": "approve",
                    "reason": "Milestone 2 live exact-content approval",
                }
            ),
            config,
        )
    result = completed["runsigil_run"]
    if result["status"] != "completed":
        raise RuntimeError(f"LangGraph run did not complete: {result}")
    return result


async def _openai_agents_proof(client: RunSigilClient, settings: AdapterSettings) -> dict[str, Any]:
    adapter = OpenAIAgentsRunSigilAdapter(client, settings)
    tool = adapter.tools()[0]
    arguments = {
        "recipient": "milestone-two@example.test",
        "amount_cents": 19,
        "description": "Milestone 2 OpenAI Agents live proof",
    }
    if tool.needs_approval is not True:
        raise RuntimeError("OpenAI Agents tool is not approval-gated")
    context = ToolContext(
        context=None,
        tool_name=tool.name,
        tool_call_id=f"live_{uuid4().hex}",
        tool_arguments=json.dumps(arguments),
    )
    with agent_invocation("runsigil-openai-agents-live", framework="openai-agents"):
        result = await tool.on_invoke_tool(context, json.dumps(arguments))
    if not isinstance(result, dict) or result.get("status") != "completed":
        raise RuntimeError(f"OpenAI Agents run did not complete: {result}")
    return result


def main() -> None:
    api_url = _required("RUNSIGIL_API_URL")
    api_key = _required("RUNSIGIL_API_KEY")
    context = _context(api_url, api_key)
    settings = AdapterSettings(
        base_url=api_url,
        api_key=api_key,
        project_id=context["projects"][0]["id"],
        environment_id=context["environments"][0]["id"],
        agent_id=context["agents"][0]["id"],
        terminal_wait_seconds=45,
    )
    with RunSigilClient(settings) as client:
        langgraph_result = _langgraph_proof(client, settings)
        openai_result = asyncio.run(_openai_agents_proof(client, settings))
    langgraph_budgets = _assert_evidence(api_url, api_key, str(langgraph_result["run_id"]))
    openai_budgets = _assert_evidence(api_url, api_key, str(openai_result["run_id"]))
    print(
        json.dumps(
            {
                "status": "milestone_two_live_proof_passed",
                "langgraph": langgraph_result,
                "openai_agents": openai_result,
                "evidence_reservations": {
                    "langgraph": langgraph_budgets,
                    "openai_agents": openai_budgets,
                },
                "raw_content_captured": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
