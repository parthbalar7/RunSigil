from __future__ import annotations

import json
from pathlib import Path

from runsigil_cli.main import app
from typer.testing import CliRunner


def _write_definition(path: Path, node_type: str = "output") -> None:
    nodes = [
        {"id": "start", "type": "input", "name": "Input"},
        {"id": "done", "type": node_type, "name": "Final node"},
    ]
    edges = [{"id": "edge", "source": "start", "target": "done"}]
    if node_type != "output":
        if node_type == "agent":
            nodes[1].update(
                {
                    "model_route_id": "10000000-0000-4000-8000-000000000001",
                    "policy_bundle_id": "10000000-0000-4000-8000-000000000002",
                    "config": {
                        "input_state_key": "model_input",
                        "result_state_key": "model_output",
                        "max_output_tokens": 128,
                    },
                }
            )
        nodes.append({"id": "output", "type": "output", "name": "Output"})
        edges.append({"id": "done_output", "source": "done", "target": "output"})
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entry_node_id": "start",
                "nodes": nodes,
                "edges": edges,
                "limits": {
                    "max_steps": 10,
                    "max_duration_seconds": 60,
                    "max_tokens": 100,
                    "max_cost_minor": 10,
                },
            }
        ),
        encoding="utf-8",
    )


def test_workflow_validate_cli_accepts_executable_definition(tmp_path: Path) -> None:
    definition = tmp_path / "workflow.json"
    _write_definition(definition)

    result = CliRunner().invoke(app, ["workflow", "validate", str(definition), "--json"])

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["valid"] is True
    assert document["executable"] is True


def test_workflow_validate_cli_accepts_serial_agent_node(tmp_path: Path) -> None:
    definition = tmp_path / "workflow.json"
    _write_definition(definition, "agent")

    result = CliRunner().invoke(app, ["workflow", "validate", str(definition), "--json"])

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["valid"] is True
    assert document["executable"] is True
    assert document["issues"] == []


def test_workflow_wait_approve_sends_exact_digest(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, str] | None]] = []

    def request(_client, method, path, *, json=None, params=None):
        calls.append((method, path, json))
        return {"status": "resolved", "resolution": "approved"}

    monkeypatch.setattr("runsigil_cli.main.Client.request", request)
    wait_id = "00000000-0000-0000-0000-000000000111"
    digest = f"sha256:{'a' * 64}"

    result = CliRunner().invoke(
        app,
        ["workflow", "wait-approve", wait_id, "--digest", digest, "--json"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "POST",
            f"/v1/workflow-waits/{wait_id}/decision",
            {"content_digest": digest, "decision": "approved"},
        )
    ]


def test_workflow_replay_posts_exact_checkpoint_and_idempotency(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def request(_client, method, path, *, json=None, params=None):
        captured.update({"method": method, "path": path, "json": json})
        return {"status": "queued", "run_kind": "workflow"}

    monkeypatch.setattr("runsigil_cli.main.Client.request", request)
    run_id = "00000000-0000-0000-0000-000000000441"
    checkpoint_id = "00000000-0000-0000-0000-000000000442"

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "replay",
            run_id,
            checkpoint_id,
            "--idempotency-key",
            "replay-exact-0001",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "method": "POST",
        "path": f"/v1/workflow-runs/{run_id}/replays",
        "json": {
            "checkpoint_id": checkpoint_id,
            "idempotency_key": "replay-exact-0001",
        },
    }


def test_run_cancel_posts_to_fenced_cancellation_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def request(_client, method, path, *, json=None, params=None):
        captured.update({"method": method, "path": path, "json": json})
        return {"status": "cancelled"}

    monkeypatch.setattr("runsigil_cli.main.Client.request", request)
    run_id = "00000000-0000-0000-0000-000000000443"

    result = CliRunner().invoke(app, ["run", "cancel", run_id, "--json"])

    assert result.exit_code == 0, result.output
    assert captured == {
        "method": "POST",
        "path": f"/v1/runs/{run_id}/cancel",
        "json": None,
    }


def test_workflow_wait_event_loads_payload(monkeypatch, tmp_path: Path) -> None:
    payload = tmp_path / "event.json"
    payload.write_text('{"ticket":"SR-42"}', encoding="utf-8")
    captured: dict[str, object] = {}

    def request(_client, method, path, *, json=None, params=None):
        captured.update({"method": method, "path": path, "json": json})
        return {"status": "resolved", "resolution": "received"}

    monkeypatch.setattr("runsigil_cli.main.Client.request", request)
    wait_id = "00000000-0000-0000-0000-000000000222"
    digest = f"sha256:{'b' * 64}"

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "wait-event",
            wait_id,
            "ticket_closed",
            str(payload),
            "--digest",
            digest,
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["json"] == {
        "content_digest": digest,
        "event_key": "ticket_closed",
        "payload": {"ticket": "SR-42"},
    }


def test_evaluation_annotate_posts_append_only_review(monkeypatch, tmp_path: Path) -> None:
    annotation = tmp_path / "annotation.json"
    annotation.write_text(
        '{"idempotency_key":"review-0001","label":"passed",'
        '"score_milli":975,"reason_codes":["human_verified"]}',
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def request(_client, method, path, *, json=None, params=None):
        captured.update({"method": method, "path": path, "json": json})
        return {"label": "passed", "score_milli": 975}

    monkeypatch.setattr("runsigil_cli.main.Client.request", request)
    result_id = "00000000-0000-0000-0000-000000000333"

    result = CliRunner().invoke(
        app,
        ["eval", "annotate", result_id, str(annotation), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "method": "POST",
        "path": f"/v1/evaluation-results/{result_id}/annotations",
        "json": {
            "idempotency_key": "review-0001",
            "label": "passed",
            "score_milli": 975,
            "reason_codes": ["human_verified"],
        },
    }
