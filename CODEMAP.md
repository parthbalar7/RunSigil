# RunSigil code map

This file maps implemented capabilities. Update it with architectural changes.

## Applications

| Path | Purpose |
| --- | --- |
| `apps/control-api/runsigil_control_api` | FastAPI boundary, authentication, tenant sessions, domain models, governed-action service, hierarchical budget reservations, dead-letter APIs, Run listing, cancellation, gateway authorization |
| `apps/control-api/alembic` | PostgreSQL schema, RLS, database roles/functions, seed bootstrap |
| `apps/worker/runsigil_worker` | Action claim, gateway dispatch, bounded ambiguity reconciliation, durable dead letters, multi-resource budget settlement, evidence finalization |
| `apps/gateway/runsigil_gateway` | Stateless MCP `2026-07-28` and A2A `1.0` ingress, control-plane client, fixed-destination validation, final authorization, audience-bound credential exchange, bounded provider request |
| `apps/web` | Accessible React operator flow and run investigation surface |

## Packages

| Path | Purpose |
| --- | --- |
| `packages/contracts/runsigil_contracts` | Canonical JSON, digests, shared strict protocol/API input contracts, decision/action wire contracts, error codes |
| `packages/policy/runsigil_policy` | Typed fail-closed policy evaluation |
| `packages/evidence/runsigil_evidence` | Domain-separated Ed25519 signing and offline verification |
| `packages/cli/runsigil_cli` | Stable JSON-capable CLI |
| `packages/sdk-python/runsigil_sdk` | Typed adapter contract, HTTP client, safe results, and GenAI agent telemetry helper |
| `packages/telemetry/runsigil_telemetry` | OTLP trace/metric setup and privacy-safe operation instrumentation |
| `adapters/langgraph/runsigil_langgraph` | Checkpointed exact-content interrupt/resume node for LangGraph `1.2.11` |
| `adapters/openai-agents/runsigil_openai_agents` | Native approval-gated function tool for OpenAI Agents `0.22.0` |

## Deployment and examples

| Path | Purpose |
| --- | --- |
| `deploy/compose` | Isolated local PostgreSQL, Redis, MinIO, collector, API, worker, gateway, web, and demo provider |
| `deploy/kind` | Dedicated `runsigil-dev` lifecycle scripts |
| `deploy/helm/runsigil` | Initial chart with service accounts, pod security, resources, PDB, NetworkPolicy, and migration Job |
| `examples/demo-provider` | Development-only external effect service that verifies audience-bound credentials and idempotency |
| `examples/governed-action` | Reproducible end-to-end driver |
| `examples/protocol-gateway` | Live MCP and A2A task, approval, execution, and cancellation proof |
| `examples/milestone-two` | Live LangGraph and OpenAI Agents adapter proof with hierarchical budget evidence |

## Tests

| Path | Proof |
| --- | --- |
| `tests/unit` | Canonicalization, policy, evidence, gateway claims, MCP/A2A, real adapter contracts, and privacy-safe GenAI telemetry |
| `tests/integration` | API flow, concurrency-safe hierarchical budgets, intent/outbox durability, bounded DLQ and reconcile-only redrive |
| `tests/security` | API/database tenant isolation, approval digest/replay rejection, deployment posture, and per-service secret boundaries |
| `examples/governed-action/live.py` | Complete API/worker/gateway/provider/evidence live proof |
| `examples/protocol-gateway/live.py` | Live MCP/A2A mapping to durable governed Runs and provider effects |
| `examples/milestone-two/live.py` | Live adapter pause/resume, execution, telemetry, and 20-reservation evidence proof |
| `apps/web/src/components/*.test.tsx` | Approval interaction, filtering correlation, and accessible textual status |

## Verification

Run `scripts/verify.ps1`. PostgreSQL security proofs require
`RUNSIGIL_TEST_DATABASE_URL` pointed only at an isolated RunSigil test database.
