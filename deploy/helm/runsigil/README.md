# RunSigil Helm chart

This chart is experimental Milestone 0 deployment infrastructure. It expects
external PostgreSQL, Redis, object storage, and a pre-created Secret named by
`global.existingSecret`. It creates no plaintext credentials.

Every image digest is mandatory. Use a dedicated namespace labeled for the
restricted Pod Security Standard. Replace the example database/provider CIDRs and
prove NetworkPolicy with the production CNI before deployment.

The chart includes distinct service accounts, non-root/read-only pod security,
resource bounds, health probes, migration Job, PDBs, optional HPAs, default-deny
NetworkPolicy, and optional TLS Ingress. It is not production-ready until the gaps in
`docs/operations/production-readiness.md` are closed.

