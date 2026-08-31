# Threat model

## Assets

- Tenant identities, delegations, policies, budgets, and approval authority.
- Exact action arguments and their content digests.
- Provider credentials and secret references.
- Run, trace, audit, and evidence integrity.
- Worker leases, idempotency keys, and reconciliation state.

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

## Residual risk

The database owner, migration path, API, worker, gateway, signing key custodian, and
provider signing key custodian are trusted. RLS is not a defense against the owner.
The development provider allows explicit private-network access and HTTP; production
configuration rejects this. Object Lock, independent timestamping, OIDC federation,
DPoP/mTLS, Kubernetes enforcement, full ABAC, containment providers, and sensor
attestation are not implemented in this milestone.
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
