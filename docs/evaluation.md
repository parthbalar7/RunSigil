# Deterministic evaluation foundation

Evaluation dataset payloads are encrypted and versioned. The API returns names,
safe classification/tags, counts, and content digests; it does not return scenario
inputs or expected outputs.

An evaluation pins:

- one active workflow deployment and immutable workflow version;
- one immutable dataset version;
- a minimum aggregate score;
- an optional completed baseline against the same dataset version;
- a maximum permitted regression.

Each scenario starts through the same durable workflow outbox/checkpoint path. The
current graders are:

- task outcome: output digest equals the expected output digest;
- trajectory: the exact executed node path equals the expected path when supplied;
- deterministic environment: workflow and dataset versions remain pinned;
- policy: every scenario-declared required node has an append-only `allow` decision;
- safety: execution succeeds, no forbidden node appears in the path, and the
  scenario maximum-step assertion is respected.

Scores range from 0 to 1000. The release gate passes only when the aggregate meets
the configured minimum and the baseline regression bound. Signed per-run evidence
contains evaluation IDs, digests, score, and status, never raw dataset content.

CLI entry points:

```powershell
runsigil eval dataset-create dataset.json --json
runsigil eval run <deployment-id> <dataset-version-id> --json
runsigil eval run <deployment-id> <dataset-version-id> --simulation-profile-id <profile-id> --json
runsigil eval get <evaluation-id> --json
runsigil eval annotate <evaluation-result-id> annotation.json --json
```

Phase 4 retains authenticated human annotations containing an idempotency key, a
`passed`, `failed`, or `needs_review` label, an optional 0-1000 score, and one or
more safe reason-code identifiers. Annotations carry exact tenant/evaluation/result/
scenario/run lineage, are append-only at the database layer, and expose no free-form
review text. They appear with evaluation results. Because review may occur after a
run's evidence was sealed, annotations are audited records and do not mutate or
claim inclusion in that original signed evidence bundle.

Scenario assertions are encrypted with the input and expected output and bound into
the immutable dataset-version digest. Required-policy and forbidden-node lists must
be unique and disjoint. Results and signed evidence expose only outcomes and digests.

Deterministic checkpoint replay reports exact match/divergence. A deployment with
the supported governed tool requires an explicit immutable simulation profile for
fork, replay, and evaluation. Each simulated tool call binds tool, arguments,
profile, and result digests and records that no side effect was performed. Live
workflow starts remain live and never select simulation implicitly. Agent-only
evaluations use the governed live fixed demo model route and consume its budgets;
general model simulation, model-based graders, shadow and production sampling,
sampling controls, annotation consensus, and multi-version regression dashboards
are not implemented in this phase.
