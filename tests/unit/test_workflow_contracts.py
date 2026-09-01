from __future__ import annotations

from runsigil_contracts import WorkflowDefinition, validate_workflow_definition


def _definition(nodes: list[dict], edges: list[dict], *, max_steps: int = 100) -> dict:
    return {
        "schema_version": 1,
        "entry_node_id": "start",
        "nodes": nodes,
        "edges": edges,
        "limits": {
            "max_steps": max_steps,
            "max_duration_seconds": 300,
            "max_tokens": 10_000,
            "max_cost_minor": 1_000,
        },
    }


def test_minimal_workflow_is_executable() -> None:
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {"id": "done", "type": "output", "name": "Output"},
            ],
            [{"id": "start_done", "source": "start", "target": "done"}],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)

    assert result.valid is True
    assert result.executable is True
    assert result.issues == []
    assert result.definition_digest.startswith("sha256:")


def test_condition_parallel_join_and_bounded_loop_validate() -> None:
    condition = {"field": "approved", "operator": "eq", "value": True}
    loop_limits = {
        "max_iterations": 2,
        "max_duration_seconds": 30,
        "max_tokens": 100,
        "max_cost_minor": 10,
    }
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {"id": "route", "type": "condition", "name": "Route", "config": condition},
                {"id": "fan", "type": "parallel", "name": "Fan out"},
                {"id": "left", "type": "condition", "name": "Left", "config": condition},
                {"id": "right", "type": "condition", "name": "Right", "config": condition},
                {"id": "join", "type": "join", "name": "Join"},
                {
                    "id": "loop",
                    "type": "bounded_loop",
                    "name": "Bounded loop",
                    "config": loop_limits,
                },
                {"id": "body", "type": "condition", "name": "Loop body", "config": condition},
                {"id": "done", "type": "output", "name": "Output"},
            ],
            [
                {"id": "e1", "source": "start", "target": "route"},
                {"id": "e2", "source": "route", "target": "fan", "branch": "true"},
                {"id": "e3", "source": "route", "target": "done", "branch": "false"},
                {"id": "e4", "source": "fan", "target": "left"},
                {"id": "e5", "source": "fan", "target": "right"},
                {"id": "e6", "source": "left", "target": "join", "branch": "true"},
                {"id": "e7", "source": "left", "target": "join", "branch": "false"},
                {"id": "e8", "source": "right", "target": "join", "branch": "true"},
                {"id": "e9", "source": "right", "target": "join", "branch": "false"},
                {"id": "e10", "source": "join", "target": "loop"},
                {"id": "e11", "source": "loop", "target": "body", "branch": "continue"},
                {"id": "e12", "source": "loop", "target": "done", "branch": "exit"},
                {"id": "e13", "source": "body", "target": "loop", "branch": "true"},
                {"id": "e14", "source": "body", "target": "loop", "branch": "false"},
            ],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)

    assert result.valid is True
    assert result.executable is True
    assert result.issues == []


def test_unbounded_cycle_is_rejected() -> None:
    condition = {"field": "repeat", "operator": "eq", "value": True}
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {"id": "route", "type": "condition", "name": "Route", "config": condition},
                {"id": "done", "type": "output", "name": "Output"},
            ],
            [
                {"id": "e1", "source": "start", "target": "route"},
                {"id": "e2", "source": "route", "target": "start", "branch": "true"},
                {"id": "e3", "source": "route", "target": "done", "branch": "false"},
            ],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)

    assert result.valid is False
    assert "unbounded_cycle" in {issue.code for issue in result.issues}


def test_serial_agent_node_is_executable_with_governed_references() -> None:
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {
                    "id": "agent",
                    "type": "agent",
                    "name": "Governed model call",
                    "model_route_id": "10000000-0000-4000-8000-000000000001",
                    "policy_bundle_id": "10000000-0000-4000-8000-000000000002",
                    "config": {
                        "input_state_key": "model_input",
                        "result_state_key": "model_output",
                        "max_output_tokens": 128,
                    },
                },
                {"id": "done", "type": "output", "name": "Output"},
            ],
            [
                {"id": "e1", "source": "start", "target": "agent"},
                {"id": "e2", "source": "agent", "target": "done"},
            ],
        )
    )

    draft = validate_workflow_definition(definition)
    deployment = validate_workflow_definition(definition, for_deployment=True)

    assert draft.valid is True
    assert draft.executable is True
    assert deployment.valid is True
    assert deployment.executable is True
    assert deployment.issues == []


def test_loop_requires_all_four_positive_limits() -> None:
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {
                    "id": "loop",
                    "type": "bounded_loop",
                    "name": "Broken loop",
                    "config": {"max_iterations": 3},
                },
                {"id": "done", "type": "output", "name": "Output"},
            ],
            [
                {"id": "e1", "source": "start", "target": "loop"},
                {"id": "e2", "source": "loop", "target": "loop", "branch": "continue"},
                {"id": "e3", "source": "loop", "target": "done", "branch": "exit"},
            ],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)

    missing_limits = [issue for issue in result.issues if issue.code == "loop_limit_missing"]
    assert len(missing_limits) == 3


def test_inline_sensitive_workflow_config_is_rejected() -> None:
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {
                    "id": "start",
                    "type": "input",
                    "name": "Input",
                    "config": {"nested": {"api_key": "must-not-be-persisted"}},
                },
                {"id": "done", "type": "output", "name": "Output"},
            ],
            [{"id": "e1", "source": "start", "target": "done"}],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)

    assert result.valid is False
    assert "sensitive_inline_config_forbidden" in {issue.code for issue in result.issues}


def test_serial_durable_wait_nodes_are_executable() -> None:
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {
                    "id": "timer",
                    "type": "timer",
                    "name": "Delay",
                    "timeout_seconds": 2,
                    "config": {"delay_seconds": 1},
                },
                {
                    "id": "approval",
                    "type": "approval",
                    "name": "Human approval",
                    "config": {"risk": "high", "reason_code": "release_review"},
                },
                {
                    "id": "information",
                    "type": "request_information",
                    "name": "Request information",
                    "config": {"state_key": "review", "reason_code": "review_details"},
                },
                {
                    "id": "event",
                    "type": "event",
                    "name": "External event",
                    "config": {"event_key": "release_ready", "state_key": "release"},
                },
                {"id": "denied", "type": "output", "name": "Denied"},
                {"id": "done", "type": "output", "name": "Done"},
            ],
            [
                {"id": "e1", "source": "start", "target": "timer"},
                {"id": "e2", "source": "timer", "target": "approval"},
                {
                    "id": "e3",
                    "source": "approval",
                    "target": "information",
                    "branch": "approved",
                },
                {
                    "id": "e4",
                    "source": "approval",
                    "target": "denied",
                    "branch": "denied",
                },
                {"id": "e5", "source": "information", "target": "event"},
                {"id": "e6", "source": "event", "target": "done"},
            ],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)

    assert result.valid is True
    assert result.executable is True
    assert result.issues == []


def test_parallel_wait_combination_is_blocked_in_phase_two() -> None:
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {"id": "fan", "type": "parallel", "name": "Fan out"},
                {
                    "id": "wait",
                    "type": "timer",
                    "name": "Delay",
                    "config": {"delay_seconds": 1},
                },
                {
                    "id": "other",
                    "type": "condition",
                    "name": "Other",
                    "config": {"field": "ok", "operator": "eq", "value": True},
                },
                {"id": "join", "type": "join", "name": "Join"},
                {"id": "done", "type": "output", "name": "Done"},
            ],
            [
                {"id": "e1", "source": "start", "target": "fan"},
                {"id": "e2", "source": "fan", "target": "wait"},
                {"id": "e3", "source": "fan", "target": "other"},
                {"id": "e4", "source": "wait", "target": "join"},
                {"id": "e5", "source": "other", "target": "join", "branch": "true"},
                {"id": "e6", "source": "other", "target": "join", "branch": "false"},
                {"id": "e7", "source": "join", "target": "done"},
            ],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)

    assert result.valid is False
    assert "parallel_wait_not_supported" in {issue.code for issue in result.issues}


def test_referenced_subworkflow_is_executable_in_a_serial_definition() -> None:
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {
                    "id": "child",
                    "type": "subworkflow",
                    "name": "Verified child",
                    "timeout_seconds": 30,
                    "config": {
                        "deployment_id": "10000000-0000-4000-8000-000000000099",
                        "result_state_key": "child_result",
                    },
                },
                {"id": "done", "type": "output", "name": "Done"},
            ],
            [
                {"id": "start_child", "source": "start", "target": "child"},
                {"id": "child_done", "source": "child", "target": "done"},
            ],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)

    assert result.valid is True
    assert result.executable is True


def test_subworkflow_requires_valid_reference_and_cannot_mix_with_parallel() -> None:
    condition = {"field": "ok", "operator": "eq", "value": True}
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {"id": "fan", "type": "parallel", "name": "Fan out"},
                {
                    "id": "child",
                    "type": "subworkflow",
                    "name": "Invalid child",
                    "config": {"deployment_id": "not-a-uuid", "result_state_key": "bad.key"},
                },
                {"id": "other", "type": "condition", "name": "Other", "config": condition},
                {"id": "done", "type": "output", "name": "Done"},
            ],
            [
                {"id": "start_fan", "source": "start", "target": "fan"},
                {"id": "fan_child", "source": "fan", "target": "child"},
                {"id": "fan_other", "source": "fan", "target": "other"},
                {"id": "child_done", "source": "child", "target": "done"},
                {"id": "other_true", "source": "other", "target": "done", "branch": "true"},
                {
                    "id": "other_false",
                    "source": "other",
                    "target": "done",
                    "branch": "false",
                },
            ],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)
    codes = {issue.code for issue in result.issues}

    assert "subworkflow_deployment_id_invalid" in codes
    assert "subworkflow_result_state_key_invalid" in codes
    assert "parallel_subworkflow_not_supported" in codes


def test_governed_tool_node_is_executable_with_state_references() -> None:
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {
                    "id": "send",
                    "type": "tool",
                    "name": "Governed send",
                    "config": {
                        "tool_id": "60000000-0000-4000-8000-000000000001",
                        "arguments_state_key": "invoice",
                        "result_state_key": "invoice_result",
                    },
                },
                {"id": "done", "type": "output", "name": "Done"},
            ],
            [
                {"id": "start_send", "source": "start", "target": "send"},
                {"id": "send_done", "source": "send", "target": "done"},
            ],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)

    assert result.valid is True
    assert result.executable is True
    assert result.issues == []


def test_tool_node_rejects_inline_arguments_and_parallel_execution() -> None:
    definition = WorkflowDefinition.model_validate(
        _definition(
            [
                {"id": "start", "type": "input", "name": "Input"},
                {"id": "fan", "type": "parallel", "name": "Fan out"},
                {
                    "id": "send",
                    "type": "tool",
                    "name": "Unsafe tool",
                    "config": {
                        "tool_id": "not-a-uuid",
                        "arguments": {"recipient": "must-not-persist@example.test"},
                        "arguments_state_key": "bad.key",
                        "result_state_key": "bad.key",
                    },
                },
                {
                    "id": "other",
                    "type": "condition",
                    "name": "Other",
                    "config": {"field": "ok", "operator": "eq", "value": True},
                },
                {"id": "done", "type": "output", "name": "Done"},
            ],
            [
                {"id": "start_fan", "source": "start", "target": "fan"},
                {"id": "fan_send", "source": "fan", "target": "send"},
                {"id": "fan_other", "source": "fan", "target": "other"},
                {"id": "send_done", "source": "send", "target": "done"},
                {"id": "other_true", "source": "other", "target": "done", "branch": "true"},
                {
                    "id": "other_false",
                    "source": "other",
                    "target": "done",
                    "branch": "false",
                },
            ],
        )
    )

    result = validate_workflow_definition(definition, for_deployment=True)
    codes = {issue.code for issue in result.issues}

    assert "sensitive_inline_config_forbidden" in codes
    assert "tool_id_invalid" in codes
    assert "tool_arguments_state_key_invalid" in codes
    assert "tool_result_state_key_invalid" in codes
    assert "parallel_tool_not_supported" in codes
