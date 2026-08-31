from __future__ import annotations

from runsigil_telemetry import Operation


def agent_invocation(agent_name: str, *, framework: str) -> Operation:
    return Operation(
        f"invoke_agent {agent_name}",
        metric_name="gen_ai.invoke_agent.duration",
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": agent_name,
            "runsigil.framework": framework,
            "runsigil.content_captured": False,
        },
    )
