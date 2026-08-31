# Architectural reference reuse assessment

RunSigil was designed after a read-only review of `D:\Projects\AgentDock`. That
repository is MIT licensed, but this delivery reuses concepts only and contains no
copied source code, imports, runtime packages, database objects, or deployment
resources. RunSigil is Apache-2.0 licensed.

| Capability studied | Source files inspected | Reuse | Licensing decision | RunSigil security change | Replacement and proof |
| --- | --- | --- | --- | --- | --- |
| Platform topology and trust boundaries | `CODEMAP.md`, `docs/architecture.md`, `docs/runtime-trust-plane.md` | Concept | Clean implementation under Apache-2.0 | Narrow first slice; explicit non-claims | `ARCHITECTURE.md`; architecture tests/static checks |
| Tenant scoping | `apps/api/berthline_api/tenancy.py`, `docs/security.md` | Concept | No code copied | Add forced PostgreSQL RLS and compound tenant FKs | control API database layer; API and direct-role isolation tests |
| Fail-closed tool policy | `apps/api/berthline_api/policy.py` | Concept | No code copied | Typed decision outcomes and outage denial | `packages/policy`; policy tests |
| Durable side effects | `docs/side-effect-actions.md`, `apps/api/berthline_api/actions/service.py`, `actions/state_machine.py` | Concept | No code copied | Persist outbox with intent; final gateway recheck; content-bound approval cannot edit args | governed-action service/worker; crash, ambiguity, replay tests |
| Evidence | `docs/strenghening/evidence-pack-external-proof-and-immutable-artifacts.md`, `apps/governance/berthline_governance/evidence.py`, `evidence_signing.py` | Concept | No code copied | Small domain-separated canonical bundle, metadata-only by default | `packages/evidence`; tamper and privacy tests |
| Workflow assurance and limits | `docs/native-workflows.md` | Neither in this milestone | No implementation reused | Workflow v2 deferred instead of shallow scaffolding | Documented unimplemented; later milestone |
| Gateway and hostile egress | `docs/mcp-a2a-gateway.md`, `docs/security-isolation.md` | Concept | No code copied | No caller bearer forwarding; fixed route; final authorization; bounded response | gateway; SSRF and credential tests |
| Deployment and residual limitations | `docs/deployment.md`, `docs/berthline-deep-research-audit.md` | Concept | No code copied | Development assets labeled honestly; production claims deferred | deployment docs and static tests |

The review also covered the reference's known limits: process execution is not a
sandbox, NetworkPolicy depends on a capable CNI, signatures do not create external
timestamps, database owners remain trusted, and provider ambiguity cannot be turned
into exactly-once behavior. RunSigil preserves these distinctions.

