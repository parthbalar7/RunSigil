# Threat model

## Assets

- Tenant identities, delegations, policies, budgets, and approval authority.
- Exact action arguments and their content digests.
- Provider credentials and secret references.
- Run, trace, audit, and evidence integrity.
- Worker leases, idempotency keys, and reconciliation state.
- Immutable workflow versions, encrypted checkpoints, dataset scenarios, and
  evaluation release gates, durable waits/subworkflows, node policy decisions,
  replay comparisons, governed workflow-tool child Runs, explicit simulation
  profiles/calls, encrypted model calls, and human review lineage.

## Adversaries

- A tenant attempting to discover or mutate another tenant's rows.
- A compromised or prompt-injected agent requesting unauthorized effects.
- A caller replaying or transplanting an approval.
- A stale worker retrying an ambiguous external action.
- A hostile provider, connector, DNS response, redirect, or oversized response.
- An operator with runtime database credentials attempting direct SQL access.
- An evidence recipient receiving modified content.

## Controls in this milestone

| Threat | Control | Proof |
| --- | --- | --- |
| Cross-tenant API access | Auth-derived organization and not-found behavior | API security test |
| Cross-tenant SQL access | Forced PostgreSQL RLS and compound FKs | Direct app-role test |
| Missing governance | Typed fail-closed policy engine | Policy outage test |
| Changed/replayed approval | Recomputed digest, expiry, atomic one-use transition | Approval security tests |
| Side effect without durable intent | Transactional intent/action/outbox and pre-I/O claim commit | Worker integration test |
| Duplicate after ambiguity | Stable key, reconcile-only recovery, bounded DLQ terminal | Crash/ambiguity and DLQ tests |
| Budget overrun | Stable-order row locks and all-scope reservations in the creation transaction | Concurrent and multi-scope budget tests |
| Unsafe operator replay | Version-fenced, audited, bounded redrive enters reconciliation only | DLQ integration test |
| Caller token forwarded | Gateway-only service auth plus audience-bound provider token | Credential-boundary test |
| Sensitive content exposure | Default metadata-only traces/evidence and boundary redaction | Content/secret tests |
| Evidence modification | Domain-separated Ed25519 signature | Tamper test |
| SSRF/redirect abuse | Operator-fixed URL, scheme/host/IP checks, no redirects, size/time bounds | Gateway unit tests |
| Protocol version/header smuggling | Exact MCP header/body parity and A2A version checks | Protocol contract tests |
| Browser-based protocol abuse | Explicit Origin allowlist | Protocol contract tests |
| Prompt or file ingestion through A2A | Exactly one structured data Part; text/raw/URL rejected | A2A contract test |
| Cross-tenant task probing | Protocol task IDs resolved through auth-derived RLS context | API and protocol tests |
| Cancellation racing an external effect | Cancellation only at the locked pre-effect approval boundary | API integration test |
| Prompt/output leakage through telemetry | Fixed metadata attributes; no raw prompt/output/action arguments | Telemetry unit test |
| Framework resumes changed content | Digest validation or native exact tool-call approval bridge | Adapter contract tests |
| Unbounded autonomous workflow | Deployment rejects cycles without bounded-loop nodes and requires four loop limits | Workflow contract tests |
| Duplicate workflow step after crash | Lease recovery replaces the claim token; stale token settlement is ignored | Workflow integration test |
| Workflow/dataset state disclosure | AES-GCM associated-data binding; API/trace/evidence expose digests only | Workflow integration privacy proof |
| Cross-tenant workflow probing | Auth-derived RLS on all workflow and evaluation tables | API and direct-role isolation test |
| Regressed workflow release | Dataset-version pinning, deterministic graders, baseline bound, and explicit release gate | Evaluation integration test |
| Replayed or transplanted wait response | Exact request/state digest, expiry, wait type, and locked single-use resolution | Workflow integration and database-trigger tests |
| Raw information/event response disclosure | AES-GCM response storage with associated-data and digest verification; metadata-only API/trace/evidence | Workflow integration privacy proof |
| Cross-tenant wait or annotation access | Compound tenant lineage, forced RLS, and auth-derived context | API and direct-role isolation test |
| Evaluation review tampering | Append-only annotation trigger, idempotency key, exact result lineage, reviewer identity, and safe reason codes | Evaluation integration trigger test |
| Cross-scope or recursive subworkflow | Same project/environment/agent validation, exact compound lineage, cycle rejection, and depth bound | Workflow deployment/integration tests |
| Subworkflow result transplant | Call-bound child execution/content/state digests and encrypted state verification | Workflow integration test |
| Cancellation leaves nested work live | Pending call settles once and an immediate child wake observes cancellation | Cancellation integration test |
| Node executes after policy loss | Runtime digest/status/expiry evaluation before behavior; only persisted `allow` advances | Policy fail-closed integration test |
| Replay is falsely reported equivalent | Immutable source checkpoint/final digests; state and full path must both match | Replay integration and trigger tests |
| Unsafe evaluation trajectory | Encrypted required-policy, forbidden-node, and maximum-step assertions | Five-grader integration test |
| Workflow tool bypasses governance | Tool node creates a child governed Run with exact intent, delegation, action policy, budgets, approval, outbox, and gateway authorization before effect | Workflow-tool integration test |
| Tool timeout races external effect | Timeout and action claim use fenced row locks; cancellation is limited to pending approval or an unclaimed action, otherwise reconciliation owns the result | Workflow-tool timeout and cancellation tests |
| Replay duplicates a tool effect | Effectful fork/replay/evaluation require an exact immutable simulation profile whose executor performs no effect | Simulation integration test |
| Simulation is selected implicitly or transplanted | Execution mode and profile are immutable, project/tool/digest bound, and required explicitly | Simulation API, RLS, and integration tests |
| Model input/output leaks at rest or through evidence | AES-GCM content binding plus metadata-only API, telemetry, audit, UI, and evidence | Agent-model integration privacy proof |
| Model call bypasses governance | Exact route/policy/delegation/budget/request lineage is persisted before a leased claim and revalidated online at the gateway | Agent-model integration test |
| Ambiguous model call is executed twice | Stable provider key and reconcile-only state machine; no blind execute retry | Model worker and live provider proof |

## Residual risk

The database owner, migration path, API, worker, gateway, signing key custodian, and
provider signing key custodian are trusted. RLS is not a defense against the owner.
The development provider allows explicit private-network access and HTTP; production
configuration rejects this. Object Lock, independent timestamping, OIDC federation,
DPoP/mTLS, Kubernetes enforcement, full ABAC, containment providers, and sensor
attestation are not implemented in this milestone.
Arbitrary agent hosting, supervisor/handoff execution, general workflow connectors,
model-based graders, general provider simulation, production sampling, annotation consensus,
and distributed scheduling of workflows containing waits, subworkflows, or tools are
not implemented. Suspended workflows and the supported tool node are serial.
MCP sessions/streams and A2A streaming/push are not advertised or implemented. The
OpenTelemetry GenAI semantic conventions used here are still development-stability
upstream. DLQ redrive is PostgreSQL polling, not a high-volume broker claim.

## Abuse cases

1. A prompt injects a new recipient after approval: digest mismatch blocks it.
2. A browser repeats an approval request: the compare-and-set sees `approved` and
   returns `RUNSIGIL_APPROVAL_REPLAYED`.
3. A worker times out after the provider commits: action becomes ambiguous and only
   the reconciliation endpoint can resolve it.
4. Policy storage is missing: no default allow exists.
5. An API key from Organization A names a Run from B: RLS hides the row.
6. A provider redirects to metadata: the gateway does not follow redirects.
7. An MCP caller supplies a different method or tool in headers and body: the
   gateway rejects it before dispatch.
8. An A2A caller tries to smuggle a prompt, URL, or raw bytes: the content type is
   rejected before creating a Run.
9. A caller cancels after approval dispatch: the control API rejects the transition
   because external I/O may already have started.
10. A provider remains unavailable through repeated receipt checks: the worker
    dead-letters the action while retaining reservations; redrive cannot execute it.
11. Two callers race the final quota unit: stable-order row locks allow one complete
    reservation set and reject the other before either provider call.
12. A workflow definition creates a cycle without a bounded loop: deployment fails
    before a Run or outbox record exists.
13. A crashed workflow worker later submits its stale token: no node attempt or
    checkpoint is appended; the valid recovery claim advances once.
14. An event sender uses a digest from another node or a wrong event key: resolution
    is rejected and the workflow remains suspended.
15. A response and timeout arrive together: row locks serialize them, so one
    terminal transition wins and no resolved wait can be consumed twice.
16. A parent references a child from another scope or creates a recursive chain:
    deployment rejects it before a Run exists.
17. A policy bundle becomes inactive after deployment: the next node fails closed
    and signs failure evidence without executing the node.
18. A replay reaches different state through a different path: its immutable record
    settles `diverged`, never `matched`.
19. A tool timeout fires after a worker claim: RunSigil does not declare failure or
    retry the effect; the parent remains suspended until receipt reconciliation.
20. A caller forks or evaluates an effectful workflow without its exact profile:
    the request is rejected before a new execution is created.
21. A stale model worker changes input or route after claiming: the gateway's final
    digest and lineage check rejects provider egress.
22. A model provider commits but the response is lost: the call enters reconciliation
    and queries the stable key instead of repeating generation.
