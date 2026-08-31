# Production readiness gap

Milestone 0 and the first Milestone 1 slice are not a production release. Before a
production claim, RunSigil requires at minimum:

- OIDC/OAuth validation and human/workload delegation;
- external secret and signing-key custody with rotation;
- customer-controlled WORM storage and independent timestamping;
- hardened Kubernetes images pinned by verified digest;
- live CNI egress proofs, ingress TLS, and scoped database roles;
- HA leases, bounded operator redrive, backup/restore, and disaster recovery tests;
- production provider connectors with documented idempotency/reconciliation;
- complete security, load, accessibility, and upgrade testing.

The Helm chart is development/experimental until these gates are closed.

