# RunSigil

> Govern every agent run.

RunSigil is an independent, open-source control plane for governing agent actions.
This repository delivers **Milestones 0-2** as a tested governed-action reference
slice. A
content-bound, approval-gated side effect can start through the CLI, web UI, MCP, or
A2A and proceed through the same tenant-scoped API, durable intent and transactional
outbox, worker, fail-closed gateway authorization, demo provider, traces, and
publicly verifiable Ed25519 evidence.

It does not share another product's database, cluster, secrets, or runtime resources,
and it does not execute arbitrary agent processes. Workflow Engine v2, enterprise
SSO, production containment, and hardened cloud deployment belong to later
milestones.

## What works now

- Scoped, SHA-256-hashed API keys and organization-derived request context.
- PostgreSQL row-level security on every tenant-owned table.
- Explicit policy decisions: `allow`, `deny`, and `require_approval` in this slice.
- Atomic hierarchical budgets across organization, project, environment, agent,
  user, and model-route scopes for currency, tokens, requests, concurrent runs,
  tool actions, and model calls.
- Content-bound, one-use, expiring approvals.
- Durable intents, actions, traces, audit rows, and transactional outbox events.
- Worker claims committed before egress; ambiguous results require reconciliation,
  then enter a durable DLQ after a configured bound.
- Version-fenced, audited, bounded, reconcile-only dead-letter redrive via API/CLI.
- Gateway final authorization and audience-bound downstream credentials.
- Stateless MCP `2026-07-28` tools/tasks mapped to durable governed Runs.
- A2A `1.0` structured task creation, exact approval follow-up, listing, and safe
  pre-effect cancellation.
- Privacy-safe OpenTelemetry GenAI agent/tool spans and duration metrics over OTLP.
- Tested LangGraph `1.2.11` and OpenAI Agents `0.22.0` adapters with native
  pause/resume approval boundaries.
- Raw action arguments excluded from traces and evidence by default.
- Canonical JSON evidence signed with Ed25519 and verified offline by the CLI.
- Accessible React operator surface for creating, approving, and inspecting a run.

## Quick start

Prerequisites: Python 3.11+ (3.13 recommended), Node 22+, and Docker Desktop or a
compatible Docker Engine.

```powershell
Copy-Item .env.example .env
# Replace every `replace-*` and `change-me-*` value in .env.
docker compose --env-file .env -f deploy/compose/compose.yaml up --build
```

After the stack is healthy, the CLI can drive the live example:

```powershell
$env:RUNSIGIL_API_URL = "http://localhost:8000"
$env:RUNSIGIL_API_KEY = "<the RUNSIGIL_BOOTSTRAP_API_KEY value>"
runsigil doctor --json
runsigil run start --amount-cents 4200 --recipient "ops@example.test" --json
runsigil approval list --json
runsigil approval approve <approval-id> --digest <content-digest> --json
runsigil run get <run-id> --json
runsigil evidence export <run-id> --output evidence.json --json
runsigil evidence verify evidence.json --json
runsigil dlq list --json
```

Or execute the same approval-to-evidence proof automatically. It asserts the
approval boundary, committed provider receipt, metadata-only response, and signed
evidence verification, then writes the ignored evidence file under `.runsigil/`:

```powershell
./examples/governed-action/run-live.ps1
```

To exercise the MCP and A2A entry points through the same live worker and provider:

```powershell
./examples/protocol-gateway/run-live.ps1
```

To exercise both real framework adapters, exact approval pause/resume, hierarchical
budget evidence, the worker/gateway/provider path, and GenAI adapter spans without an
external model API key:

```powershell
./examples/milestone-two/run-live.ps1
```

The operator UI is at `http://localhost:3000`. See
[`docs/operations/local-development.md`](docs/operations/local-development.md)
for exact bootstrap and recovery steps.

## Security posture

This is a deliberately narrow, tested slice—not a production process executor.
The demo provider and development credentials are examples and are rejected by
production configuration. Production readiness still requires external secret
custody, OIDC, customer WORM storage, hardened Kubernetes, independent timestamping,
production connectors, and completion of later milestones. Report vulnerabilities according to
[`SECURITY.md`](SECURITY.md).

## Development

```powershell
uv sync --all-extras
uv run ruff check .
uv run mypy
uv run pytest
npm --prefix apps/web ci
npm --prefix apps/web test
npm --prefix apps/web run build
```

`./scripts/verify.ps1` runs the local static, unit, UI, build, and dependency
checks as one repeatable command. Pass `-IncludeIntegration` after setting the
five isolated test database variables documented in the script.

Architecture and current implementation locations are maintained in
[`ARCHITECTURE.md`](ARCHITECTURE.md) and [`CODEMAP.md`](CODEMAP.md).
