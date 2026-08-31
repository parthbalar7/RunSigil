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
| Gateway | Stateless protocol ingress plus fixed-route guarded egress | General connectors are not implemented |
| Worker | Durable action executor/reconciler | Not a general agent process executor |
| Web UI | Governed run and approval operator flow | Broader product surfaces are intentionally absent |
| Kubernetes and cloud | Development manifests only | Milestone 5 |
| Milestone 2 reference slice | Implemented | MCP/A2A ingress, telemetry, hierarchical budgets, DLQ, and two framework adapters |

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

## Trust boundaries and non-claims

The API, worker, gateway, database owner, evidence signer, and configured provider
token issuer are trusted components. PostgreSQL RLS constrains runtime roles but
does not constrain the database owner. The example provider demonstrates protocol
behavior; it is not a production connector. This slice does not claim exactly-once
external effects, arbitrary agent isolation, kernel sensing, external timestamping,
MCP server sessions/streams, A2A streaming/push, or production readiness.
OpenTelemetry GenAI semantic conventions are emitted at their current upstream
development stability and may require future compatibility updates.
