from __future__ import annotations

import re
from collections import defaultdict
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from runsigil_contracts.canonical import canonical_digest


class WorkflowNodeType(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    AGENT = "agent"
    SUPERVISOR = "supervisor"
    TOOL = "tool"
    CONDITION = "condition"
    PARALLEL = "parallel"
    JOIN = "join"
    BOUNDED_LOOP = "bounded_loop"
    SUBWORKFLOW = "subworkflow"
    TIMER = "timer"
    EVENT = "event"
    HANDOFF = "handoff"
    APPROVAL = "approval"
    REQUEST_INFORMATION = "request_information"


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    type: WorkflowNodeType
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)
    model_route_id: UUID | None = None
    policy_bundle_id: UUID | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    retry_limit: int = Field(default=0, ge=0, le=10)
    position: tuple[float, float] | None = None


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    source: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    target: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    branch: Literal["default", "true", "false", "continue", "exit", "approved", "denied"] = (
        "default"
    )


class WorkflowLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(ge=1, le=10_000)
    max_duration_seconds: int = Field(ge=1, le=604_800)
    max_tokens: int = Field(ge=1)
    max_cost_minor: int = Field(ge=1)


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    entry_node_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    nodes: list[WorkflowNode] = Field(min_length=2, max_length=1_000)
    edges: list[WorkflowEdge] = Field(min_length=1, max_length=5_000)
    limits: WorkflowLimits


class WorkflowValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    location: str
    severity: Literal["error", "warning"] = "error"


class WorkflowValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    executable: bool
    definition_digest: str
    issues: list[WorkflowValidationIssue]


EXECUTABLE_NODE_TYPES = frozenset(
    {
        WorkflowNodeType.INPUT,
        WorkflowNodeType.OUTPUT,
        WorkflowNodeType.AGENT,
        WorkflowNodeType.CONDITION,
        WorkflowNodeType.PARALLEL,
        WorkflowNodeType.JOIN,
        WorkflowNodeType.BOUNDED_LOOP,
        WorkflowNodeType.SUBWORKFLOW,
        WorkflowNodeType.TOOL,
        WorkflowNodeType.TIMER,
        WorkflowNodeType.EVENT,
        WorkflowNodeType.APPROVAL,
        WorkflowNodeType.REQUEST_INFORMATION,
    }
)

WAIT_NODE_TYPES = frozenset(
    {
        WorkflowNodeType.TIMER,
        WorkflowNodeType.EVENT,
        WorkflowNodeType.APPROVAL,
        WorkflowNodeType.REQUEST_INFORMATION,
    }
)

FORBIDDEN_CONFIG_KEYS = frozenset(
    {
        "api_key",
        "arguments",
        "authorization",
        "output",
        "password",
        "prompt",
        "secret",
        "token",
    }
)


def _forbidden_config_paths(value: Any, prefix: str = "config") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            path = f"{prefix}.{key}"
            if normalized in FORBIDDEN_CONFIG_KEYS:
                paths.append(path)
            paths.extend(_forbidden_config_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_forbidden_config_paths(item, f"{prefix}[{index}]"))
    return paths


def _issue(
    issues: list[WorkflowValidationIssue],
    code: str,
    message: str,
    location: str,
    *,
    severity: Literal["error", "warning"] = "error",
) -> None:
    issues.append(
        WorkflowValidationIssue(
            code=code,
            message=message,
            location=location,
            severity=severity,
        )
    )


def _valid_key(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,99}", value) is not None


def _has_cycle_without_loop(nodes: set[str], edges: list[WorkflowEdge]) -> bool:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.source in nodes and edge.target in nodes:
            adjacency[edge.source].append(edge.target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        if any(visit(target) for target in adjacency[node_id]):
            return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in sorted(nodes) if node_id not in visited)


def validate_workflow_definition(
    definition: WorkflowDefinition,
    *,
    for_deployment: bool = False,
) -> WorkflowValidationResult:
    issues: list[WorkflowValidationIssue] = []
    nodes_by_id: dict[str, WorkflowNode] = {}
    for index, node in enumerate(definition.nodes):
        if node.id in nodes_by_id:
            _issue(
                issues,
                "duplicate_node_id",
                f"Node id '{node.id}' is duplicated.",
                f"nodes[{index}].id",
            )
        nodes_by_id[node.id] = node

    edge_ids: set[str] = set()
    outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
    incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for index, edge in enumerate(definition.edges):
        if edge.id in edge_ids:
            _issue(
                issues,
                "duplicate_edge_id",
                f"Edge id '{edge.id}' is duplicated.",
                f"edges[{index}].id",
            )
        edge_ids.add(edge.id)
        if edge.source not in nodes_by_id:
            _issue(
                issues,
                "unknown_edge_source",
                f"Edge source '{edge.source}' does not exist.",
                f"edges[{index}].source",
            )
        if edge.target not in nodes_by_id:
            _issue(
                issues,
                "unknown_edge_target",
                f"Edge target '{edge.target}' does not exist.",
                f"edges[{index}].target",
            )
        if edge.source in nodes_by_id and edge.target in nodes_by_id:
            outgoing[edge.source].append(edge)
            incoming[edge.target].append(edge)

    entry = nodes_by_id.get(definition.entry_node_id)
    if entry is None:
        _issue(
            issues,
            "entry_missing",
            "The entry node does not exist.",
            "entry_node_id",
        )
    elif entry.type != WorkflowNodeType.INPUT:
        _issue(
            issues,
            "entry_not_input",
            "The entry node must be an input node.",
            "entry_node_id",
        )
    elif incoming[entry.id]:
        _issue(
            issues,
            "entry_has_incoming_edge",
            "The input entry node cannot have incoming edges.",
            f"nodes.{entry.id}",
        )

    output_nodes = [node for node in definition.nodes if node.type == WorkflowNodeType.OUTPUT]
    if not output_nodes:
        _issue(issues, "output_missing", "At least one output node is required.", "nodes")

    for node in definition.nodes:
        node_outgoing = outgoing[node.id]
        node_incoming = incoming[node.id]
        location = f"nodes.{node.id}"
        for forbidden_path in _forbidden_config_paths(node.config):
            _issue(
                issues,
                "sensitive_inline_config_forbidden",
                "Sensitive workflow content must use an external reference, not inline config.",
                f"{location}.{forbidden_path}",
            )
        if node.type not in EXECUTABLE_NODE_TYPES:
            _issue(
                issues,
                "node_type_not_executable",
                (
                    f"Node type '{node.type.value}' is modeled but is not executable in the "
                    "current Workflow Engine v2 slice."
                ),
                location,
                severity="error" if for_deployment else "warning",
            )
        if node.type == WorkflowNodeType.OUTPUT:
            if node_outgoing:
                _issue(
                    issues,
                    "output_has_outgoing_edge",
                    "Output nodes cannot have outgoing edges.",
                    location,
                )
        elif node.type == WorkflowNodeType.CONDITION:
            branches = sorted(edge.branch for edge in node_outgoing)
            if branches != ["false", "true"]:
                _issue(
                    issues,
                    "condition_branches_invalid",
                    "Condition nodes require exactly one true and one false edge.",
                    location,
                )
            if node.config.get("operator") not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                _issue(
                    issues,
                    "condition_operator_invalid",
                    "Condition nodes require an eq, ne, gt, gte, lt, or lte operator.",
                    f"{location}.config.operator",
                )
            if not isinstance(node.config.get("field"), str) or not node.config.get("field"):
                _issue(
                    issues,
                    "condition_field_missing",
                    "Condition nodes require a non-empty input field.",
                    f"{location}.config.field",
                )
            if "value" not in node.config:
                _issue(
                    issues,
                    "condition_value_missing",
                    "Condition nodes require a comparison value.",
                    f"{location}.config.value",
                )
        elif node.type == WorkflowNodeType.PARALLEL:
            if len(node_outgoing) < 2 or any(edge.branch != "default" for edge in node_outgoing):
                _issue(
                    issues,
                    "parallel_fanout_invalid",
                    "Parallel nodes require at least two default outgoing edges.",
                    location,
                )
        elif node.type == WorkflowNodeType.JOIN:
            if len(node_incoming) < 2 or len(node_outgoing) != 1:
                _issue(
                    issues,
                    "deterministic_join_invalid",
                    "Join nodes require at least two incoming edges and exactly one outgoing edge.",
                    location,
                )
        elif node.type == WorkflowNodeType.BOUNDED_LOOP:
            branches = sorted(edge.branch for edge in node_outgoing)
            if branches != ["continue", "exit"]:
                _issue(
                    issues,
                    "loop_branches_invalid",
                    "Bounded loops require exactly one continue and one exit edge.",
                    location,
                )
            for key in ("max_iterations", "max_duration_seconds", "max_tokens", "max_cost_minor"):
                value = node.config.get(key)
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    _issue(
                        issues,
                        "loop_limit_missing",
                        f"Bounded loops require a positive integer {key} limit.",
                        f"{location}.config.{key}",
                    )
        elif node.type == WorkflowNodeType.AGENT:
            if node.model_route_id is None:
                _issue(
                    issues,
                    "agent_model_route_missing",
                    "Agent nodes require a catalog model_route_id.",
                    f"{location}.model_route_id",
                )
            if node.policy_bundle_id is None:
                _issue(
                    issues,
                    "agent_policy_missing",
                    "Agent nodes require an explicit fail-closed policy_bundle_id.",
                    f"{location}.policy_bundle_id",
                )
            if not _valid_key(node.config.get("input_state_key")):
                _issue(
                    issues,
                    "agent_input_state_key_invalid",
                    "Agent nodes require an input_state_key for encrypted run state.",
                    f"{location}.config.input_state_key",
                )
            if not _valid_key(node.config.get("result_state_key")):
                _issue(
                    issues,
                    "agent_result_state_key_invalid",
                    "Agent nodes require a result_state_key for encrypted model output.",
                    f"{location}.config.result_state_key",
                )
            max_output_tokens = node.config.get("max_output_tokens")
            if (
                not isinstance(max_output_tokens, int)
                or isinstance(max_output_tokens, bool)
                or max_output_tokens <= 0
                or max_output_tokens > 32_768
            ):
                _issue(
                    issues,
                    "agent_max_output_tokens_invalid",
                    "Agent nodes require max_output_tokens between 1 and 32768.",
                    f"{location}.config.max_output_tokens",
                )
            if len(node_outgoing) != 1 or node_outgoing[0].branch != "default":
                _issue(
                    issues,
                    "agent_edge_invalid",
                    "Agent nodes require exactly one default outgoing edge.",
                    location,
                )
        elif node.type == WorkflowNodeType.TOOL:
            tool_id = node.config.get("tool_id")
            try:
                UUID(str(tool_id))
            except (TypeError, ValueError, AttributeError):
                _issue(
                    issues,
                    "tool_id_invalid",
                    "Tool nodes require a catalog tool_id UUID.",
                    f"{location}.config.tool_id",
                )
            if not _valid_key(node.config.get("arguments_state_key")):
                _issue(
                    issues,
                    "tool_arguments_state_key_invalid",
                    "Tool nodes require an arguments_state_key for encrypted run state.",
                    f"{location}.config.arguments_state_key",
                )
            if not _valid_key(node.config.get("result_state_key")):
                _issue(
                    issues,
                    "tool_result_state_key_invalid",
                    "Tool nodes require a result_state_key for the safe result projection.",
                    f"{location}.config.result_state_key",
                )
            if len(node_outgoing) != 1 or node_outgoing[0].branch != "default":
                _issue(
                    issues,
                    "tool_edge_invalid",
                    "Tool nodes require exactly one default outgoing edge.",
                    location,
                )
        elif node.type == WorkflowNodeType.SUBWORKFLOW:
            deployment_id = node.config.get("deployment_id")
            try:
                UUID(str(deployment_id))
            except (TypeError, ValueError, AttributeError):
                _issue(
                    issues,
                    "subworkflow_deployment_id_invalid",
                    "Subworkflow nodes require an immutable deployment_id UUID.",
                    f"{location}.config.deployment_id",
                )
            if not _valid_key(node.config.get("result_state_key")):
                _issue(
                    issues,
                    "subworkflow_result_state_key_invalid",
                    "Subworkflow nodes require a result_state_key for encrypted child state.",
                    f"{location}.config.result_state_key",
                )
            if len(node_outgoing) != 1 or node_outgoing[0].branch != "default":
                _issue(
                    issues,
                    "subworkflow_edge_invalid",
                    "Subworkflow nodes require exactly one default outgoing edge.",
                    location,
                )
        elif node.type == WorkflowNodeType.TIMER:
            delay = node.config.get("delay_seconds")
            if (
                not isinstance(delay, int)
                or isinstance(delay, bool)
                or delay <= 0
                or delay > node.timeout_seconds
            ):
                _issue(
                    issues,
                    "timer_delay_invalid",
                    "Timer delay_seconds must be positive and no greater than timeout_seconds.",
                    f"{location}.config.delay_seconds",
                )
            if len(node_outgoing) != 1 or node_outgoing[0].branch != "default":
                _issue(
                    issues,
                    "timer_edge_invalid",
                    "Timer nodes require exactly one default outgoing edge.",
                    location,
                )
        elif node.type == WorkflowNodeType.EVENT:
            if not _valid_key(node.config.get("event_key")):
                _issue(
                    issues,
                    "event_key_invalid",
                    "Event nodes require a stable event_key identifier.",
                    f"{location}.config.event_key",
                )
            if not _valid_key(node.config.get("state_key")):
                _issue(
                    issues,
                    "event_state_key_invalid",
                    "Event nodes require a state_key for encrypted event data.",
                    f"{location}.config.state_key",
                )
            if len(node_outgoing) != 1 or node_outgoing[0].branch != "default":
                _issue(
                    issues,
                    "event_edge_invalid",
                    "Event nodes require exactly one default outgoing edge.",
                    location,
                )
        elif node.type == WorkflowNodeType.APPROVAL:
            branches = sorted(edge.branch for edge in node_outgoing)
            if branches != ["approved", "denied"]:
                _issue(
                    issues,
                    "approval_branches_invalid",
                    "Approval nodes require exactly one approved and one denied edge.",
                    location,
                )
            if node.config.get("risk") not in {"low", "medium", "high", "critical"}:
                _issue(
                    issues,
                    "approval_risk_invalid",
                    "Approval nodes require a low, medium, high, or critical risk.",
                    f"{location}.config.risk",
                )
            if not _valid_key(node.config.get("reason_code")):
                _issue(
                    issues,
                    "approval_reason_code_invalid",
                    "Approval nodes require a stable reason_code identifier.",
                    f"{location}.config.reason_code",
                )
        elif node.type == WorkflowNodeType.REQUEST_INFORMATION:
            if not _valid_key(node.config.get("state_key")):
                _issue(
                    issues,
                    "information_state_key_invalid",
                    "Request-information nodes require a state_key for the encrypted response.",
                    f"{location}.config.state_key",
                )
            if not _valid_key(node.config.get("reason_code")):
                _issue(
                    issues,
                    "information_reason_code_invalid",
                    "Request-information nodes require a stable reason_code identifier.",
                    f"{location}.config.reason_code",
                )
            if len(node_outgoing) != 1 or node_outgoing[0].branch != "default":
                _issue(
                    issues,
                    "information_edge_invalid",
                    "Request-information nodes require exactly one default outgoing edge.",
                    location,
                )
        elif len(node_outgoing) != 1:
            _issue(
                issues,
                "node_outgoing_edge_invalid",
                "This node type requires exactly one outgoing edge.",
                location,
            )

    if entry is not None:
        reachable: set[str] = set()
        pending = [entry.id]
        while pending:
            current = pending.pop()
            if current in reachable:
                continue
            reachable.add(current)
            pending.extend(edge.target for edge in outgoing[current])
        for node_id in sorted(set(nodes_by_id).difference(reachable)):
            _issue(
                issues,
                "node_unreachable",
                f"Node '{node_id}' is not reachable from the entry node.",
                f"nodes.{node_id}",
            )
        if output_nodes and not any(node.id in reachable for node in output_nodes):
            _issue(
                issues,
                "output_unreachable",
                "No output node is reachable from the entry node.",
                "nodes",
            )

    non_loop_nodes = {
        node.id for node in definition.nodes if node.type != WorkflowNodeType.BOUNDED_LOOP
    }
    if _has_cycle_without_loop(non_loop_nodes, definition.edges):
        _issue(
            issues,
            "unbounded_cycle",
            "Every graph cycle must pass through a bounded-loop node.",
            "edges",
        )

    if any(node.type == WorkflowNodeType.PARALLEL for node in definition.nodes) and any(
        node.type in WAIT_NODE_TYPES for node in definition.nodes
    ):
        _issue(
            issues,
            "parallel_wait_not_supported",
            "Phase 2 wait nodes cannot be combined with parallel fan-out in one definition.",
            "nodes",
        )
    if any(node.type == WorkflowNodeType.PARALLEL for node in definition.nodes) and any(
        node.type == WorkflowNodeType.SUBWORKFLOW for node in definition.nodes
    ):
        _issue(
            issues,
            "parallel_subworkflow_not_supported",
            "Durable subworkflow nodes cannot yet be combined with parallel fan-out.",
            "nodes",
        )
    if any(node.type == WorkflowNodeType.PARALLEL for node in definition.nodes) and any(
        node.type == WorkflowNodeType.TOOL for node in definition.nodes
    ):
        _issue(
            issues,
            "parallel_tool_not_supported",
            "Durable effectful tool nodes cannot yet be combined with parallel fan-out.",
            "nodes",
        )
    if any(node.type == WorkflowNodeType.PARALLEL for node in definition.nodes) and any(
        node.type == WorkflowNodeType.AGENT for node in definition.nodes
    ):
        _issue(
            issues,
            "parallel_agent_not_supported",
            "Durable agent model calls cannot yet be combined with parallel fan-out.",
            "nodes",
        )
    if any(node.type == WorkflowNodeType.TOOL for node in definition.nodes) and any(
        node.type == WorkflowNodeType.AGENT for node in definition.nodes
    ):
        _issue(
            issues,
            "agent_tool_mixed_not_supported",
            "Agent model calls and effectful tool nodes cannot yet share one definition.",
            "nodes",
        )

    has_errors = any(issue.severity == "error" for issue in issues)
    has_unsupported = any(issue.code == "node_type_not_executable" for issue in issues)
    return WorkflowValidationResult(
        valid=not has_errors,
        executable=not has_errors and not has_unsupported,
        definition_digest=canonical_digest(definition),
        issues=issues,
    )
