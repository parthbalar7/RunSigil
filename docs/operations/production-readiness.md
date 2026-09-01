# Production readiness gap

The Milestone 0-2 reference slice and Milestone 3 phase-seven workflow slice are not
a production release. Before a production claim, RunSigil requires at minimum:

- OIDC/OAuth validation and human/workload delegation;
- external secret and signing-key custody with rotation;
- customer-controlled WORM storage and independent timestamping;
- hardened Kubernetes images pinned by verified digest;
- live CNI egress proofs, ingress TLS, and scoped database roles;
- HA leases, production DLQ operations, backup/restore, and disaster recovery tests;
- production provider connectors with documented idempotency/reconciliation;
- complete security, load, accessibility, and upgrade testing;
- production model connectors and isolated agent hosting, supervisor/handoff nodes,
  general connector tool contracts beyond the fixed governed demo tool, scalable scheduling for waits
  and tools within parallel branches, external event source authentication beyond
  scoped API keys, general provider-backed replay simulation, evaluation sampling, and
  production grader/annotation operations.

The Helm chart is development/experimental until these gates are closed.
