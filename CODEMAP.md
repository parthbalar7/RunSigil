# RunSigil code map

This file maps implemented capabilities. Update it with architectural changes.

## Applications

| Path | Purpose |
| --- | --- |
| `apps/control-api/runsigil_control_api` | FastAPI boundary, authentication, tenant sessions, domain models, governed-action service, tenant-scoped Run listing, pre-effect cancellation, internal gateway authorization |
| `apps/control-api/alembic` | PostgreSQL schema, RLS, database roles/functions, seed bootstrap |
| `apps/worker/runsigil_worker` | Action claim, gateway dispatch, ambiguity handling, reconciliation, evidence finalization |
| `apps/gateway/runsigil_gateway` | Stateless MCP `2026-07-28` and A2A `1.0` ingress, control-plane client, fixed-destination validation, final authorization, audience-bound credential exchange, bounded provider request |
| `apps/web` | Accessible React operator flow and run investigation surface |

## Packages

| Path | Purpose |
| --- | --- |
| `packages/contracts/runsigil_contracts` | Canonical JSON, digests, shared strict protocol/API input contracts, decision/action wire contracts, error codes |
| `packages/policy/runsigil_policy` | Typed fail-closed policy evaluation |
| `packages/evidence/runsigil_evidence` | Domain-separated Ed25519 signing and offline verification |
| `packages/cli/runsigil_cli` | Stable JSON-capable CLI |

## Deployment and examples

| Path | Purpose |
| --- | --- |
| `deploy/compose` | Isolated local PostgreSQL, Redis, MinIO, collector, API, worker, gateway, web, and demo provider |
| `deploy/kind` | Dedicated `runsigil-dev` lifecycle scripts |
| `deploy/helm/runsigil` | Initial chart with service accounts, pod security, resources, PDB, NetworkPolicy, and migration Job |
| `examples/demo-provider` | Development-only external effect service that verifies audience-bound credentials and idempotency |
| `examples/governed-action` | Reproducible end-to-end driver |
| `examples/protocol-gateway` | Live MCP and A2A task, approval, execution, and cancellation proof |

## Tests

| Path | Proof |
| --- | --- |
| `tests/unit` | Canonicalization, policy fail-closed behavior, evidence tamper detection, gateway credential claims, MCP/A2A protocol contracts |
| `tests/integration` | API action flow, budget-before-call, durable intent/outbox, worker ambiguity and idempotency |
| `tests/security` | API/database tenant isolation, approval digest/replay rejection, deployment posture, and per-service secret boundaries |
| `examples/governed-action/live.py` | Complete API/worker/gateway/provider/evidence live proof |
| `examples/protocol-gateway/live.py` | Live MCP/A2A mapping to durable governed Runs and provider effects |
| `apps/web/src/components/*.test.tsx` | Approval interaction, filtering correlation, and accessible textual status |

## Verification

Run `scripts/verify.ps1`. PostgreSQL security proofs require
`RUNSIGIL_TEST_DATABASE_URL` pointed only at an isolated RunSigil test database.
