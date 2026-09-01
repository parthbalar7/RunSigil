# RunSigil code map

This file maps implemented capabilities. Update it with architectural changes.

## Applications

| Path | Purpose |
| --- | --- |
| `apps/control-api/runsigil_control_api` | FastAPI boundary, tenant governance, durable workflows, explicit tool-simulation lineage, encrypted/budgeted model calls, replay, and deterministic evaluations |
| `apps/control-api/alembic` | PostgreSQL schema, RLS, database roles/functions, seed bootstrap |
| `apps/worker/runsigil_worker` | Action/model dispatch and reconciliation plus leased workflow advancement, simulation, settlement, checkpointing, and evidence signing |
| `apps/gateway/runsigil_gateway` | Stateless protocol ingress plus fixed tool/model destinations, final authorization, audience-bound credentials, and bounded provider requests |
| `apps/web` | Accessible React operator flow and run investigation surface |

## Packages

| Path | Purpose |
| --- | --- |
| `packages/contracts/runsigil_contracts` | Canonical JSON, digests, strict protocol/action contracts, typed Workflow Engine v2 graph, semantic validator, and error codes |
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
| `examples/demo-provider` | Development-only effect and deterministic model service with audience-bound credentials and idempotency reconciliation |
| `examples/governed-action` | Reproducible end-to-end driver |
| `examples/protocol-gateway` | Live MCP and A2A task, approval, execution, and cancellation proof |
| `examples/milestone-two` | Live LangGraph and OpenAI Agents adapter proof with hierarchical budget evidence |
| `examples/milestone-three` | Live durable workflow, deterministic fan-in, bounded loop, checkpoint fork, evaluation gate, and evidence proof |
| `examples/milestone-three-phase-two` | Live serial timer/approval/information/event waits and append-only evaluation annotation proof |
| `examples/milestone-three-phase-four` | Live referenced subworkflow, per-node policy, replay match, policy/safety evaluation, and signed evidence proof |
| `examples/milestone-three-phase-five` | Live workflow tool, exact child approval, provider effect, and linked child/parent evidence proof |
| `examples/milestone-three-phase-seven` | Live explicit tool simulation and encrypted, budgeted agent-model execution proof |

## Tests

| Path | Proof |
| --- | --- |
| `tests/unit` | Canonicalization, policy, evidence, protocols/adapters/telemetry, workflow semantic validation, and workflow CLI behavior |
| `tests/integration` | Governed actions, budgets/DLQ, durable workflows, explicit effect simulation, encrypted/budgeted model calls, replay, five graders, and annotations |
| `tests/security` | API/database tenant isolation including workflow/evaluation tables, approval replay rejection, deployment posture, and secret boundaries |
| `examples/governed-action/live.py` | Complete API/worker/gateway/provider/evidence live proof |
| `examples/protocol-gateway/live.py` | Live MCP/A2A mapping to durable governed Runs and provider effects |
| `examples/milestone-two/live.py` | Live adapter pause/resume, execution, telemetry, and 20-reservation evidence proof |
| `examples/milestone-three/live.py` | Live version/deploy/run/fork/evaluate workflow proof with signed evidence |
| `examples/milestone-three-phase-two/live.py` | Live content-bound workflow wait and human annotation proof |
| `examples/milestone-three-phase-four/live.py` | Live nested workflow/policy/replay/evaluation evidence proof |
| `examples/milestone-three-phase-five/live.py` | Live governed workflow tool and linked evidence proof |
| `examples/milestone-three-phase-seven/live.py` | Live simulated effect fork and fixed-route agent model-call/evidence proof |
| `apps/web/src/components/*.test.tsx` | Approval interaction, filtering correlation, and accessible textual status |

## Verification

Run `scripts/verify.ps1`. PostgreSQL security proofs require
`RUNSIGIL_TEST_DATABASE_URL` pointed only at an isolated RunSigil test database.
