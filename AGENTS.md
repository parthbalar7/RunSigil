# RunSigil repository guidance

RunSigil is independent. Never import from, write to, start, stop, migrate, or reuse
the database, containers, clusters, namespaces, secrets, volumes, or runtime
resources of `D:\Projects\AgentDock`.

Preserve these invariants:

1. Tenant identity comes from authenticated context, never request bodies.
2. Every tenant-owned row has `organization_id`; keep RLS and compound tenant FKs.
3. Protected actions fail closed on missing, invalid, stale, or unavailable policy.
4. Persist exact intent, content digest, decision, delegation, budget reservation,
   idempotency key, and outbox record before side effects.
5. Never blind-retry an ambiguous effect. Reconcile it.
6. Approvals are exact-content, expiring, and single use.
7. Do not record raw prompts, outputs, or action arguments by default.
8. Store secret references only; never expose secret values in logs, traces, APIs,
   evidence, fixtures, or committed environment files.
9. Evidence uses canonical JSON and asymmetric signatures.
10. Do not claim later-milestone or production capabilities that are not implemented.

Use `RUNSIGIL_*`, `runsigil`, `runsigil.io`, `@runsigil/*`, and RunSigil resource
names exclusively. Update `ARCHITECTURE.md`, `CODEMAP.md`, ADRs, tests, and the
implementation-status table together when a capability changes.

Run narrow tests while editing, then `scripts/verify.ps1`. Inspect `git diff
--check`, search for foreign product names outside `docs/reuse-assessment.md`, and
check for credentials before handing work off.

