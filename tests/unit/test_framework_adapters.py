from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
from agents.tool_context import ToolContext
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from runsigil_langgraph import LangGraphRunSigilAdapter, RunSigilGraphState
from runsigil_openai_agents import OpenAIAgentsRunSigilAdapter
from runsigil_sdk import AdapterSettings, RunSigilClient

DIGEST = "sha256:" + "a" * 64


def _settings() -> AdapterSettings:
    return AdapterSettings(
        base_url="http://runsigil.test",
        api_key="rsk_test_adapter_key_000001",
        project_id=UUID("20000000-0000-4000-8000-000000000001"),
        environment_id=UUID("30000000-0000-4000-8000-000000000001"),
        agent_id=UUID("50000000-0000-4000-8000-000000000001"),
        terminal_wait_seconds=0,
    )


def _run(status: str) -> dict[str, Any]:
    return {
        "id": "90000000-0000-4000-8000-000000000001",
        "status": status,
        "active_node": "human-approval" if status == "waiting_for_approval" else None,
        "error_code": None,
        "action": {"state": "proposed", "content_digest": DIGEST},
        "approval": {
            "id": "91000000-0000-4000-8000-000000000001",
            "status": "pending",
            "content_digest": DIGEST,
            "risk": "high",
            "reason": "Exact approval required",
            "request_preview": {"recipient": "te***@example.test"},
            "expires_at": "2026-09-01T00:00:00Z",
        },
        "evidence_status": "pending",
    }


def test_langgraph_adapter_interrupts_and_resumes_exact_content() -> None:
    decisions: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Bearer rsk_")
        if request.url.path == "/v1/runs":
            return httpx.Response(202, json=_run("waiting_for_approval"))
        if request.url.path.endswith("/decision"):
            body = json.loads(request.content)
            decisions.append(body)
            return httpx.Response(200, json=_run("completed"))
        raise AssertionError(f"unexpected adapter request {request.url.path}")

    client = RunSigilClient(_settings(), transport=httpx.MockTransport(handler))
    adapter = LangGraphRunSigilAdapter(client, _settings())
    builder = StateGraph(RunSigilGraphState)
    builder.add_node("governed_action", adapter.node)
    builder.add_edge(START, "governed_action")
    builder.add_edge("governed_action", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "runsigil-adapter-test"}}
    state: RunSigilGraphState = {
        "runsigil_action": {
            "recipient": "adapter@example.test",
            "amount_cents": 25,
            "description": "Adapter test",
            "idempotency_key": "langgraph-adapter-test-001",
        }
    }

    interrupted = graph.invoke(state, config)
    assert interrupted["__interrupt__"][0].value["content_digest"] == DIGEST
    completed = graph.invoke(
        Command(
            resume={
                "content_digest": DIGEST,
                "decision": "approve",
                "reason": "Reviewed in LangGraph",
            }
        ),
        config,
    )
    assert completed["runsigil_run"]["status"] == "completed"
    assert decisions == [
        {
            "content_digest": DIGEST,
            "decision": "approve",
            "reason": "Reviewed in LangGraph",
        }
    ]
    assert adapter.manifest().native_interruptions is True


@pytest.mark.asyncio
async def test_openai_agents_tool_bridges_native_approval_to_runsigil() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.url.path, body))
        if request.url.path == "/v1/runs":
            return httpx.Response(202, json=_run("waiting_for_approval"))
        if request.url.path.endswith("/decision"):
            return httpx.Response(200, json=_run("completed"))
        raise AssertionError(f"unexpected adapter request {request.url.path}")

    client = RunSigilClient(_settings(), transport=httpx.MockTransport(handler))
    adapter = OpenAIAgentsRunSigilAdapter(client, _settings())
    tool = adapter.tools()[0]
    arguments = {
        "recipient": "adapter@example.test",
        "amount_cents": 25,
        "description": "Adapter test",
    }
    context = ToolContext(
        context=None,
        tool_name=tool.name,
        tool_call_id="call_adapter_001",
        tool_arguments=json.dumps(arguments),
    )

    result = await tool.on_invoke_tool(context, json.dumps(arguments))
    assert tool.needs_approval is True
    assert set(tool.params_json_schema["properties"]) == set(arguments)
    assert result["status"] == "completed"
    assert calls[0][1]["idempotency_key"] == "openai-agents-call_adapter_001"
    assert calls[1][1]["content_digest"] == DIGEST
    assert calls[1][1]["decision"] == "approve"
    assert adapter.manifest().exact_content_approval is True
