# Architecture

## Delivery status

| Area | Status | Boundary |
| --- | --- | --- |
| Repository, docs, threat model, CI | Implemented | Milestone 0 |
| Governed transactional action | Implemented | Milestone 1 vertical slice |
| API-key authentication and tenant RLS | Implemented | OIDC and enterprise federation are later work |
| Policy | Implemented for allow/deny/approval | ABAC expansion and external policy bundles are later work |
| Budget | Implemented | Organization/project/environment/agent/user/model-route scopes; currency, token, request, concurrency, tool-action, and model-call units |
| Evidence | Ed25519 signed canonical JSON | WORM/timestamp export interfaces only; no production sink yet |
| Protocol ingress | MCP `2026-07-28` and A2A `1.0` first vertical slice | Stateless tasks over durable Runs; optional streams and push are not implemented |
| OpenTelemetry | Implemented | Privacy-safe GenAI agent/tool spans and duration metrics over OTLP; semantic conventions remain development-stability upstream |
| Dead-letter queue | Implemented | Durable inspection and version-fenced bounded reconcile-only redrive for unresolved effects |
| Framework adapters | Implemented | LangGraph `1.2.11` and OpenAI Agents `0.22.0`; adapters govern actions but do not host arbitrary agents |
| Gateway | Stateless protocol ingress plus fixed-route guarded tool/model egress | General connectors and arbitrary model providers are not implemented |
| Worker | Durable action and serial workflow-model executor/reconciler | Not a general agent process host |
| Web UI | Governed run and approval operator flow | Broader product surfaces are intentionally absent |
| Kubernetes and cloud | Development manifests only | Milestone 5 |
| Milestone 2 reference slice | Implemented | MCP/A2A ingress, telemetry, hierarchical budgets, DLQ, and two framework adapters |
| Workflow Engine v2 | Milestone 3 phases 6-7 implemented | Earlier deterministic graph features plus explicit tool simulation and serial governed agent-model nodes |
| Evaluation | Milestone 3 phase 6 implemented | Encrypted datasets and five graders plus explicit deterministic simulation for the supported effectful tool |

## System shape

```text
CLI / React operator UI       MCP / A2A clients       Framework adapters
          |
          |                       |
          | hashed API key       | scoped bearer API key
          |                       v
          |              stateless protocol gateway
          |                       |
          |   organization derived by the control API
          v
Control API -------- PostgreSQL (authoritative state + RLS)
    |                        |
    | same transaction       +-- intent, decision, reservation,
    |                            action, approval, trace, audit, outbox
    v
transactional outbox (PostgreSQL, polled in this slice)
                             |
                             v
                    dedicated action worker
                             |
                   bounded reconciliation
                             |
                  durable dead-letter record
                             |
                    commit `executing` claim
                             |
                             v
                        egress gateway
                             |
                 final online authorization
                             |
                 audience-bound short-lived token
                             |
                             v
                    example external provider

API / worker / gateway / adapters --OTLP--> OpenTelemetry collector

Workflow API --version/deploy--> workflow outbox --> workflow worker
     |                                      |
     +-- encrypted state/checkpoints <------+-- deterministic node attempt
     |
     +-- dataset evaluation --> release gate --> signed workflow evidence
```

The API creates a `Run`, exact `Intent`, canonical action digest, `PolicyDecision`,
all applicable `BudgetReservation` rows, `Action`, trace events, audit event, and
`OutboxEvent` in one database transaction. Budget rows are locked in a stable order,
so concurrent requests cannot both spend the same remaining quota. If approval is
required, the action is not executable until a one-use `ApprovalRequest` bound to the
same digest is accepted.

The worker claims only approved actions from PostgreSQL. Redis is present in the
development topology for the later notification fast path, but is not authority and
is not needed for correctness in this slice. It commits the claim and lease before any
network call. The gateway asks the API to revalidate action state, content digest,
policy decision, approval, budget reservation, and lease immediately before egress.
The gateway never forwards the caller's API key; it mints a short-lived credential
whose audience is the fixed provider. Provider ambiguity moves the action to
`reconciliation_required`, never to success and never to a blind retry. Repeated
unknown reconciliation outcomes reach a durable `DeadLetter`; an operator may use a
version-fenced bounded redrive that performs reconciliation only. Active reservations
stay held while the effect remains unknown.

MCP and A2A ingress reuse the same Run UUID and control-plane state; the gateway has
no protocol task database. Exact-content approval responses call the existing
single-use approval transition. Cancellation is accepted only at the pre-effect
approval boundary; once work is queued or executing, it fails closed.

LangGraph maps its checkpointed interrupt/resume boundary to the exact RunSigil
approval. The OpenAI Agents adapter uses a native function-tool interruption and
bridges the approved tool call to the RunSigil approval under the authenticated API
actor. Neither adapter records raw prompts or model outputs. OTLP spans use GenAI
`invoke_agent` and `execute_tool` operation names plus RunSigil correlation IDs; raw
arguments, prompts, and outputs are absent from span attributes by default.

Workflow versions are immutable and deployment is stricter than draft validation.
The worker processes one deterministic node per committed outbox step and
appends an encrypted checkpoint. Parallel branches use stable node-ID scheduling and
join only after every predecessor has completed. Cycles are rejected unless they
pass through a bounded loop with mandatory iteration, duration, token, and cost
limits. Expired claims may be recovered, while a stale claim token cannot settle a
step. Checkpoint forks create a new Run with explicit parent lineage.

Phase 2 wait nodes persist their exact request lineage and a scheduled wake outbox
record before suspension. Timers resume at their due time. Approval, information,
and event responses must match the exact wait digest, are consumed once, and race
timeouts under database locks. Information and event payloads are encrypted and
bound to the wait before a fresh outbox record resumes the worker. The current
scheduler supports wait nodes only in serial workflows; deployment rejects a graph
that combines a wait with parallel fan-out.

Referenced subworkflows are serial durable calls to an active deployment in the
same project, environment, and agent scope. Deployment recursively verifies the
referenced definition digest, rejects cycles, and caps nesting at eight levels. At
runtime the worker atomically creates a child Run/execution and an immutable call
record before suspending the parent. Child settlement publishes an exact call-bound
resume; only a completed child state whose encrypted content and digests verify is
inserted under the declared parent state key. Timeouts and child failures fail the
parent closed. An authenticated cancellation is allowed only while a deterministic
workflow is queued or waiting. Pending waits/calls become terminal, children are
woken to observe cancellation, and the worker signs the final metadata-only evidence.

Phase 5 tool nodes reference the supported catalog tool plus encrypted-state input
and result keys. At the node boundary, the workflow worker creates a separate child
governed-action Run in the same transaction as an immutable `WorkflowToolCall` and
scheduled timeout. That child follows the existing delegation, action policy,
multi-scope budget, exact approval, action outbox, gateway authorization,
audience-bound credential, reconciliation, and DLQ path. The parent resumes only
after terminal child settlement and verifies the action, intent, result, and child
evidence digests before storing the safe result projection. A timeout may cancel
only a pending approval or an unclaimed action; once dispatch is possible, the
parent waits for reconciliation. Effectful graphs require an explicit immutable
simulation profile for checkpoint fork, replay, and evaluation. The deterministic
simulator validates the tool and argument contracts, stores argument/tool/profile/
result digests in an append-only call, and returns a receipt stating no side effect
occurred. Live starts still use the governed child Run. Parallel and nested-
subworkflow tool execution remain rejected.

Phase 7 serial `agent` nodes reference an active model route and fail-closed policy.
Before egress, the worker persists the exact request/route digests, policy decision,
delegation, multi-scope budget reservations, idempotency key, encrypted request,
model-call row, and outbox records. It commits a leased claim before the gateway's
final online authorization. The fixed demo route receives a model-call-bound
audience credential; completed output is encrypted at rest and only its digest and
usage metadata are exposed. Uncertain outcomes reconcile by idempotency key rather
than blind execution. Agent nodes are serial and cannot yet share a graph with tool
nodes or run in referenced children.

Nodes may reference an active policy bundle in the workflow project. Before any
node behavior, the worker evaluates the typed `workflow.node.execute` context and
persists an append-only decision binding node, execution sequence, input digest,
policy digest, expiry, effect, and reason. Only `allow` advances; every other effect
and missing, stale, invalid, or unavailable policy fails closed. Checkpoint replay
creates a new execution with immutable source lineage and settles `matched` only
when both final state and full trajectory digests equal the completed source.

Evaluation datasets store encrypted scenario input, expected output, and assertions.
Results contain only task, trajectory, environment/version, policy, safety scores,
and digests. Policy assertions require persisted `allow` decisions at named nodes;
safety assertions reject forbidden path nodes, excess steps, and failed execution. A release
gate can compare against a completed baseline for the same dataset version.
Authenticated reviewers may add idempotent, append-only labels, scores, and safe
reason codes. These later annotations are audited but do not rewrite the already
sealed per-run evidence. This is not the full unified evaluation system.

## Data and tenancy

PostgreSQL is authoritative. Redis may wake workers but cannot create or authorize
work. Each tenant-owned table has `organization_id`. Compound foreign keys carry
tenant identity through the graph. Row-level security compares each row to the
transaction-local `runsigil.organization_id` setting. The API authenticates keys
through a narrow `SECURITY DEFINER` function and then installs tenant context.

Organization identifiers are not accepted by tenant API schemas. Cross-tenant
resources return `404` to avoid disclosing existence. The migration owner is never a
runtime credential.

## Security and privacy

- Policy selection is fail closed. Missing, disabled, invalid, ambiguous, or
  unavailable decisions create no executable action.
- Action content is canonicalized with the explicitly documented deterministic
  subset used by this slice (sorted UTF-8 JSON object keys, finite numbers, and
  typed timestamp/UUID normalization) and hashed with SHA-256. It does not claim a
  complete RFC 8785 implementation.
- Approval checks recompute the current argument digest, require an exact submitted
  digest, reject expiry, and atomically consume the approval once.
- Raw action arguments are stored only in the action row for execution. API output,
  traces, audit, and evidence expose a redacted preview and the content digest.
- Workflow runtime state, checkpoints, and evaluation scenario payloads are
  encrypted with content-bound associated data. Raw values are absent from API
  responses, traces, audits, evaluation results, and evidence.
- Evidence signs a domain-separated digest of canonical JSON with Ed25519. The
  public key and key ID travel with the bundle for offline integrity verification;
  production trust-root pinning remains required.
- All error responses use stable `RUNSIGIL_*` codes. Secrets are redacted by field
  name and value at output boundaries.

## Failure semantics

| Failure | Result |
| --- | --- |
| Policy missing/unavailable | Run request denied; no intent capable of egress |
| Budget exhausted | Blocked before provider call |
| Approval digest mismatch/expired/replayed | Denied; action remains non-executable |
| Crash before claim commit | Action remains approved and may be claimed |
| Crash after claim, before receipt | Lease expires; reconcile provider state |
| Provider returns ambiguity or times out | `reconciliation_required`; only provider reconciliation follows |
| Reconciliation remains unknown at the configured bound | Durable `dead_lettered` action/run; reservations remain held |
| Operator redrives an open dead letter | Version-fenced, audited reconciliation only; bounded count |
| Provider confirms committed | Store redacted receipt; complete action/run |
| Provider confirms absent | Terminal failure; no blind execution redrive |
| API unavailable during gateway recheck | Gateway denies egress |
| Evidence content modified | Offline signature verification fails |
| MCP header/body version, method, or tool mismatch | Request rejected before dispatch |
| Untrusted browser Origin or unsupported A2A Part | Request rejected before Run creation |
| Cancellation after dispatch could race an effect | Cancellation denied; later fenced cancellation required |
| Workflow contains an unsupported node or unbounded cycle | Deployment rejected with node/edge validation issues |
| Workflow worker crashes after claim | Lease expires; a new token reclaims the step and the stale token cannot settle it |
| Workflow exceeds step or deadline bound | Run fails closed and seals failure evidence |
| Checkpoint state or definition is modified | Digest and associated-data verification fail closed |
| Evaluation misses quality or regression threshold | Evaluation completes with a failed release gate |
| Wait response digest, type, event key, or state lineage differs | Response rejected; workflow stays suspended |
| Wait response is replayed or races its timeout | One locked terminal transition wins; the other fails closed |
| Workflow wait expires | Run fails closed and seals failure evidence |
| Referenced deployment is stale, cross-scope, cyclic, or too deeply nested | Parent deployment is rejected |
| Subworkflow result lineage/digest differs or the call expires | Parent fails closed and seals evidence |
| Node policy is missing, invalid, stale, unavailable, or non-allow | Node does not execute; run fails closed |
| Replay final state or trajectory differs from the source | Replay settles `diverged`; evidence preserves both digests |
| Queued/waiting workflow is cancelled | Pending waits/calls cancel atomically; worker seals signed evidence |
| Workflow tool approval expires before dispatch | Child action and reservations cancel; parent fails with signed pre-effect timeout evidence |
| Workflow tool outcome is ambiguous | Parent remains suspended; receipt reconciliation and bounded DLQ/redrive own the outcome |
| Effectful fork/replay/evaluation omits or mismatches its simulation profile | Request fails closed before a new execution is created |
| Model policy, route, delegation, budget, claim, or request digest is stale | Gateway denies model-provider egress |
| Model provider outcome is ambiguous | Durable model call reconciles by idempotency key; execution is never blindly repeated |
| Cancellation follows model-call queueing | Cancellation is denied so external-call ownership cannot be orphaned |

## Trust boundaries and non-claims

The API, worker, gateway, database owner, evidence signer, and configured provider
token issuer are trusted components. PostgreSQL RLS constrains runtime roles but
does not constrain the database owner. The example provider demonstrates protocol
behavior; it is not a production connector. This slice does not claim exactly-once
external effects, arbitrary agent isolation, kernel sensing, external timestamping,
MCP server sessions/streams, A2A streaming/push, or production readiness.
OpenTelemetry GenAI semantic conventions are emitted at their current upstream
development stability and may require future compatibility updates.
Supervisor and handoff nodes remain non-executable. Agent nodes support one serial,
fixed demo model route; they do not host arbitrary agent processes. Tool nodes and
simulation are limited to the fixed governed transactional demo-provider contract.
Wait, subworkflow, tool, and model suspension is serial-only, agent/tool mixing is
rejected, and the web surface is an investigation view rather than an editor.
