from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer
from runsigil_evidence import verify

from runsigil_cli.client import ApiError, Client

app = typer.Typer(help="Govern every agent run.", no_args_is_help=True)
system_app = typer.Typer(help="Inspect registered AI systems.")
run_app = typer.Typer(help="Start and inspect governed runs.")
approval_app = typer.Typer(help="Review exact-content approvals.")
evidence_app = typer.Typer(help="Export and verify signed evidence.")
dlq_app = typer.Typer(help="Inspect and safely redrive ambiguous actions.")
app.add_typer(system_app, name="system")
app.add_typer(run_app, name="run")
app.add_typer(approval_app, name="approval")
app.add_typer(evidence_app, name="evidence")
app.add_typer(dlq_app, name="dlq")


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
