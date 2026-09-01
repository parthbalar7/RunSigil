# ADR 0010: Durable Workflow Engine v2 foundation

## Status

Accepted for Milestone 3 phase 1.

## Decision

Persist tenant-owned `Workflow` records and immutable `WorkflowVersion` definitions.
Only a version that passes deployment-time semantic validation may create an active
`WorkflowDeployment`. The first executable node set is deliberately limited to
input, output, condition, parallel fan-out, deterministic fan-in, and bounded loop.
Agent, supervisor, tool, subworkflow, timer, event, handoff, approval, and
request-information nodes are represented by the typed contract but fail deployment
until their durable trust boundary is implemented.

Every run creates a `WorkflowExecution` and `workflow.ready` outbox record in the
same transaction. A worker commits a lease before processing exactly one
deterministic node. Each successful node transition appends a `WorkflowNodeAttempt`,
`TraceEvent`, and encrypted `RunCheckpoint`, then publishes the next outbox record.
Expired claims may be recovered; the stale claim token cannot settle a step.

Workflow cycles are invalid unless every cycle passes through a bounded-loop node.
Loop definitions must specify positive iteration, duration, token, and cost limits.
Global duration and step limits are also mandatory and enforced at runtime.

Runtime input and evaluation scenario payloads are encrypted at rest with
content-bound associated data. APIs, traces, audits, checkpoints, evaluation
results, and signed evidence expose only digests and safe metadata. Inline workflow
config with prompt, output, argument, credential, secret, or token fields is
rejected.

Checkpoint forks decrypt and verify the source checkpoint, rebind the state to a new
execution, retain an explicit parent checkpoint, and publish a new durable run.

## Evaluation decision

Immutable dataset versions contain encrypted scenario inputs and expected outputs.
An evaluation pins the exact workflow, deployment, and dataset versions. The first
grader set is deterministic task outcome, exact trajectory, and environment/version
pinning. Aggregate scores drive a configurable release gate and optional completed
baseline comparison. Evaluation results contain digests, scores, and outcomes only.

## Consequences

- PostgreSQL remains the execution authority; Redis is not needed for correctness.
- Parallel branches are processed in stable node-ID order in this phase, providing
  deterministic fan-in without claiming distributed parallel execution.
- Checkpoint and evaluation evidence is independently signed using the existing
  Ed25519 evidence boundary.
- Model calls and external effects are not simulated. Those node types remain
  non-executable until they can reuse policy, delegation, budgets, approvals,
  reconciliation, and exact effect lineage.
- Human annotation, timers, external events, and human workflow waits are delegated
  to ADR 0011. Shadow/production sampling, advanced graders, subworkflows, and the
  interactive workflow editor remain later Milestone 3 phases.
