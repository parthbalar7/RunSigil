from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer
from pydantic import ValidationError
from runsigil_contracts import WorkflowDefinition, validate_workflow_definition
from runsigil_evidence import verify

from runsigil_cli.client import ApiError, Client

app = typer.Typer(help="Govern every agent run.", no_args_is_help=True)
system_app = typer.Typer(help="Inspect registered AI systems.")
run_app = typer.Typer(help="Start and inspect governed runs.")
approval_app = typer.Typer(help="Review exact-content approvals.")
evidence_app = typer.Typer(help="Export and verify signed evidence.")
dlq_app = typer.Typer(help="Inspect and safely redrive ambiguous actions.")
workflow_app = typer.Typer(help="Validate, version, deploy, and run durable workflows.")
evaluation_app = typer.Typer(help="Create datasets and run deterministic evaluations.")
app.add_typer(system_app, name="system")
app.add_typer(run_app, name="run")
app.add_typer(approval_app, name="approval")
app.add_typer(evidence_app, name="evidence")
app.add_typer(dlq_app, name="dlq")
app.add_typer(workflow_app, name="workflow")
app.add_typer(evaluation_app, name="eval")


def _emit(value: Any, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))
    elif isinstance(value, dict):
        for key, item in value.items():
            typer.echo(f"{key}: {item}")
    else:
        typer.echo(str(value))


def _exit_for_error(error: ApiError, *, json_output: bool) -> None:
    _emit(error.body, json_output=json_output)
    code = error.body.get("code", "")
    if error.status_code in {401, 403}:
        raise typer.Exit(4 if "AUTH" in code else 5)
    if error.status_code == 0 or error.status_code >= 500:
        raise typer.Exit(3)
    raise typer.Exit(2)


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _emit(
            {"code": "RUNSIGIL_INPUT_INVALID", "message": str(exc)},
            json_output=True,
        )
        raise typer.Exit(2) from exc
    if not isinstance(value, dict):
        _emit(
            {
                "code": "RUNSIGIL_INPUT_INVALID",
                "message": "The JSON document must be an object.",
            },
            json_output=True,
        )
        raise typer.Exit(2)
    return value


@app.command()
def doctor(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    client = Client()
    try:
        ready = client.request("GET", "/ready")
        context = client.request("GET", "/v1/context")
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(
        {
            "status": "ok",
            "api": ready,
            "organization": context["organization"],
            "checks": {"authentication": "ok", "database": ready.get("database")},
        },
        json_output=json_output,
    )


@system_app.command("list")
def system_list(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        data = Client().request("GET", "/v1/context")
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(data["systems"], json_output=json_output)


@workflow_app.command("validate")
def workflow_validate(
    definition_file: Path,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        definition = WorkflowDefinition.model_validate(_load_json_file(definition_file))
    except ValidationError as exc:
        _emit(
            {
                "valid": False,
                "executable": False,
                "issues": exc.errors(include_url=False),
            },
            json_output=True,
        )
        raise typer.Exit(2) from exc
    result = validate_workflow_definition(definition, for_deployment=True)
    _emit(result.model_dump(mode="json"), json_output=json_output)
    if not result.executable:
        raise typer.Exit(2)


@workflow_app.command("create")
def workflow_create(
    definition_file: Path,
    slug: Annotated[str, typer.Option()],
    name: Annotated[str, typer.Option()],
    description: Annotated[str, typer.Option()] = "",
    project_id: Annotated[UUID | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    client = Client()
    try:
        context = client.request("GET", "/v1/context")
        selected_project = project_id or UUID(context["projects"][0]["id"])
        result = client.request(
            "POST",
            "/v1/workflows",
            json={
                "project_id": str(selected_project),
                "slug": slug,
                "name": name,
                "description": description,
                "definition": _load_json_file(definition_file),
            },
        )
    except (ApiError, IndexError) as exc:
        if isinstance(exc, ApiError):
            _exit_for_error(exc, json_output=json_output)
        _emit(
            {"code": "RUNSIGIL_CONTEXT_EMPTY", "message": "No project is registered."},
            json_output=True,
        )
        raise typer.Exit(2) from exc
    _emit(result, json_output=json_output)


@workflow_app.command("deploy")
def workflow_deploy(
    version_id: UUID,
    environment_id: Annotated[UUID | None, typer.Option()] = None,
    agent_id: Annotated[UUID | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    client = Client()
    try:
        context = client.request("GET", "/v1/context")
        selected_environment = environment_id or UUID(context["environments"][0]["id"])
        selected_agent = agent_id or UUID(context["agents"][0]["id"])
        result = client.request(
            "POST",
            f"/v1/workflow-versions/{version_id}/deployments",
            json={
                "environment_id": str(selected_environment),
                "agent_id": str(selected_agent),
            },
        )
    except (ApiError, IndexError) as exc:
        if isinstance(exc, ApiError):
            _exit_for_error(exc, json_output=json_output)
        _emit(
            {
                "code": "RUNSIGIL_CONTEXT_EMPTY",
                "message": "No environment or agent is registered.",
            },
            json_output=True,
        )
        raise typer.Exit(2) from exc
    _emit(result, json_output=json_output)


@workflow_app.command("run")
def workflow_run(
    deployment_id: UUID,
    input_file: Path,
    idempotency_key: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request(
            "POST",
            f"/v1/workflow-deployments/{deployment_id}/runs",
            json={
                "input": _load_json_file(input_file),
                "idempotency_key": idempotency_key or f"workflow-{secrets.token_urlsafe(12)}",
            },
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@workflow_app.command("simulation-profile-create")
def workflow_simulation_profile_create(
    tool_id: UUID,
    name: Annotated[str, typer.Option()],
    project_id: Annotated[UUID | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    client = Client()
    try:
        context = client.request("GET", "/v1/context")
        selected_project = project_id or UUID(context["projects"][0]["id"])
        result = client.request(
            "POST",
            "/v1/workflow-simulation-profiles",
            json={
                "project_id": str(selected_project),
                "tool_id": str(tool_id),
                "name": name,
            },
        )
    except (ApiError, IndexError) as exc:
        if isinstance(exc, ApiError):
            _exit_for_error(exc, json_output=json_output)
        _emit(
            {"code": "RUNSIGIL_CONTEXT_EMPTY", "message": "No project is registered."},
            json_output=True,
        )
        raise typer.Exit(2) from exc
    _emit(result, json_output=json_output)


@workflow_app.command("simulation-profile-list")
def workflow_simulation_profile_list(
    project_id: Annotated[UUID | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    client = Client()
    try:
        context = client.request("GET", "/v1/context")
        selected_project = project_id or UUID(context["projects"][0]["id"])
        result = client.request(
            "GET",
            "/v1/workflow-simulation-profiles",
            params={"project_id": str(selected_project)},
        )
    except (ApiError, IndexError) as exc:
        if isinstance(exc, ApiError):
            _exit_for_error(exc, json_output=json_output)
        _emit(
            {"code": "RUNSIGIL_CONTEXT_EMPTY", "message": "No project is registered."},
            json_output=True,
        )
        raise typer.Exit(2) from exc
    _emit(result, json_output=json_output)


@workflow_app.command("fork")
@run_app.command("fork")
def workflow_fork(
    run_id: UUID,
    checkpoint_id: UUID,
    simulation_profile_id: Annotated[UUID | None, typer.Option()] = None,
    idempotency_key: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        payload = {
            "checkpoint_id": str(checkpoint_id),
            "idempotency_key": idempotency_key or f"workflow-fork-{secrets.token_urlsafe(12)}",
        }
        if simulation_profile_id is not None:
            payload["simulation_profile_id"] = str(simulation_profile_id)
        result = Client().request(
            "POST",
            f"/v1/workflow-runs/{run_id}/forks",
            json=payload,
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@workflow_app.command("replay")
@run_app.command("replay")
def workflow_replay(
    run_id: UUID,
    checkpoint_id: UUID,
    simulation_profile_id: Annotated[UUID | None, typer.Option()] = None,
    idempotency_key: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        payload = {
            "checkpoint_id": str(checkpoint_id),
            "idempotency_key": idempotency_key or f"workflow-replay-{secrets.token_urlsafe(12)}",
        }
        if simulation_profile_id is not None:
            payload["simulation_profile_id"] = str(simulation_profile_id)
        result = Client().request(
            "POST",
            f"/v1/workflow-runs/{run_id}/replays",
            json=payload,
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@workflow_app.command("wait-get")
def workflow_wait_get(
    wait_id: UUID,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request("GET", f"/v1/workflow-waits/{wait_id}")
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


def _decide_workflow_wait(
    wait_id: UUID,
    digest: str,
    decision: str,
    json_output: bool,
) -> None:
    try:
        result = Client().request(
            "POST",
            f"/v1/workflow-waits/{wait_id}/decision",
            json={"content_digest": digest, "decision": decision},
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@workflow_app.command("wait-approve")
def workflow_wait_approve(
    wait_id: UUID,
    digest: Annotated[str, typer.Option(help="Exact wait content digest.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decide_workflow_wait(wait_id, digest, "approved", json_output)


@workflow_app.command("wait-deny")
def workflow_wait_deny(
    wait_id: UUID,
    digest: Annotated[str, typer.Option(help="Exact wait content digest.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decide_workflow_wait(wait_id, digest, "denied", json_output)


@workflow_app.command("wait-information")
def workflow_wait_information(
    wait_id: UUID,
    information_file: Path,
    digest: Annotated[str, typer.Option(help="Exact wait content digest.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request(
            "POST",
            f"/v1/workflow-waits/{wait_id}/information",
            json={
                "content_digest": digest,
                "information": _load_json_file(information_file),
            },
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@workflow_app.command("wait-event")
def workflow_wait_event(
    wait_id: UUID,
    event_key: str,
    payload_file: Path,
    digest: Annotated[str, typer.Option(help="Exact wait content digest.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request(
            "POST",
            f"/v1/workflow-waits/{wait_id}/event",
            json={
                "content_digest": digest,
                "event_key": event_key,
                "payload": _load_json_file(payload_file),
            },
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@evaluation_app.command("dataset-create")
def evaluation_dataset_create(
    dataset_file: Path,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request(
            "POST",
            "/v1/evaluation-datasets",
            json=_load_json_file(dataset_file),
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@evaluation_app.command("run")
def evaluation_run(
    deployment_id: UUID,
    dataset_version_id: UUID,
    minimum_score_milli: Annotated[int, typer.Option(min=0, max=1_000)] = 1_000,
    maximum_regression_milli: Annotated[int, typer.Option(min=0, max=1_000)] = 0,
    baseline_evaluation_id: Annotated[UUID | None, typer.Option()] = None,
    simulation_profile_id: Annotated[UUID | None, typer.Option()] = None,
    idempotency_key: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        payload = {
            "deployment_id": str(deployment_id),
            "dataset_version_id": str(dataset_version_id),
            "minimum_score_milli": minimum_score_milli,
            "maximum_regression_milli": maximum_regression_milli,
            "baseline_evaluation_id": (
                str(baseline_evaluation_id) if baseline_evaluation_id else None
            ),
            "idempotency_key": idempotency_key or f"evaluation-{secrets.token_urlsafe(12)}",
        }
        if simulation_profile_id is not None:
            payload["simulation_profile_id"] = str(simulation_profile_id)
        result = Client().request(
            "POST",
            "/v1/evaluations",
            json=payload,
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@evaluation_app.command("get")
def evaluation_get(
    evaluation_id: UUID,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request("GET", f"/v1/evaluations/{evaluation_id}")
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@evaluation_app.command("annotate")
def evaluation_annotate(
    result_id: UUID,
    annotation_file: Path,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request(
            "POST",
            f"/v1/evaluation-results/{result_id}/annotations",
            json=_load_json_file(annotation_file),
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@run_app.command("start")
def run_start(
    amount_cents: Annotated[int, typer.Option(min=1, max=100_000)],
    recipient: Annotated[str, typer.Option()],
    description: Annotated[str, typer.Option()] = "Approved invoice notification",
    idempotency_key: Annotated[str | None, typer.Option()] = None,
    project_id: Annotated[UUID | None, typer.Option()] = None,
    environment_id: Annotated[UUID | None, typer.Option()] = None,
    agent_id: Annotated[UUID | None, typer.Option()] = None,
    simulate_outcome: Annotated[str, typer.Option()] = "committed",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    client = Client()
    try:
        context = client.request("GET", "/v1/context")
        selected_project = project_id or UUID(context["projects"][0]["id"])
        selected_environment = environment_id or UUID(context["environments"][0]["id"])
        selected_agent = agent_id or UUID(context["agents"][0]["id"])
        result = client.request(
            "POST",
            "/v1/runs",
            json={
                "project_id": str(selected_project),
                "environment_id": str(selected_environment),
                "agent_id": str(selected_agent),
                "recipient": recipient,
                "amount_cents": amount_cents,
                "description": description,
                "idempotency_key": idempotency_key or f"cli-{secrets.token_urlsafe(12)}",
                "simulate_outcome": simulate_outcome,
            },
        )
    except (ApiError, IndexError) as exc:
        if isinstance(exc, ApiError):
            _exit_for_error(exc, json_output=json_output)
        _emit(
            {
                "code": "RUNSIGIL_CONTEXT_EMPTY",
                "message": "No project, environment, or agent is registered.",
            },
            json_output=True,
        )
        raise typer.Exit(2) from exc
    _emit(result, json_output=json_output)


@run_app.command("get")
def run_get(
    run_id: UUID,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request("GET", f"/v1/runs/{run_id}")
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@run_app.command("cancel")
def run_cancel(
    run_id: UUID,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request("POST", f"/v1/runs/{run_id}/cancel")
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@approval_app.command("list")
def approval_list(
    status: Annotated[str, typer.Option()] = "pending",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request("GET", "/v1/approvals", params={"status": status})
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


def _decide(approval_id: UUID, digest: str, decision: str, reason: str, json_output: bool) -> None:
    try:
        result = Client().request(
            "POST",
            f"/v1/approvals/{approval_id}/decision",
            json={"content_digest": digest, "decision": decision, "reason": reason},
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@approval_app.command("approve")
def approval_approve(
    approval_id: UUID,
    digest: Annotated[str, typer.Option(help="Exact content digest shown on the request.")],
    reason: Annotated[str, typer.Option()] = "Reviewed and approved",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decide(approval_id, digest, "approve", reason, json_output)


@approval_app.command("deny")
def approval_deny(
    approval_id: UUID,
    digest: Annotated[str, typer.Option(help="Exact content digest shown on the request.")],
    reason: Annotated[str, typer.Option()] = "Denied by operator",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _decide(approval_id, digest, "deny", reason, json_output)


@dlq_app.command("list")
def dlq_list(
    status: Annotated[str, typer.Option()] = "open",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request("GET", "/v1/dead-letters", params={"status": status})
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@dlq_app.command("redrive")
def dlq_redrive(
    dead_letter_id: UUID,
    expected_version: Annotated[int, typer.Option(min=1)],
    reason: Annotated[str, typer.Option()] = "Operator requested bounded reconciliation",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request(
            "POST",
            f"/v1/dead-letters/{dead_letter_id}/redrive",
            json={"expected_version": expected_version, "reason": reason},
        )
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    _emit(result, json_output=json_output)


@evidence_app.command("export")
def evidence_export(
    run_id: UUID,
    output: Annotated[Path, typer.Option()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = Client().request("GET", f"/v1/runs/{run_id}/evidence")
    except ApiError as exc:
        _exit_for_error(exc, json_output=json_output)
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    _emit(
        {"status": "exported", "path": str(output), "content_digest": result["content_digest"]},
        json_output=json_output,
    )


@evidence_app.command("verify")
def evidence_verify(
    evidence_file: Path,
    trusted_key_b64: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        document = json.loads(evidence_file.read_text(encoding="utf-8"))
        trusted = {document["signing_key_id"]: trusted_key_b64} if trusted_key_b64 else None
        result = verify(document, trusted_public_keys=trusted)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        _emit({"valid": False, "message": str(exc)}, json_output=True)
        raise typer.Exit(6) from exc
    _emit(result.model_dump(), json_output=json_output)
    if not result.valid:
        raise typer.Exit(6)


if __name__ == "__main__":
    app()
