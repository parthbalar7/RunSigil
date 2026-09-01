# ADR 0012: Referenced subworkflows and idle cancellation

## Status

Accepted for Milestone 3 phase 3.

## Decision

Execute a subworkflow as a durable serial call to a referenced deployment, not as
an embedded graph copy. The parent definition stores only the deployment UUID and a
safe result-state key. Parent deployment recursively validates that every referenced
deployment is active, digest-valid, executable, and uses the same project,
environment, and agent. Reference cycles are rejected and nesting is capped at eight
levels.

On first reaching the node, the worker creates the child Run, execution, initial
checkpoint, ready outbox, immutable `WorkflowSubworkflowCall`, timeout outbox,
trace, and audit records in one transaction before clearing the parent lease. The
call binds exact parent/child execution and Run IDs, deployment, sequence, input
state digest, child execution content digest, result key, and expiry. A child
terminal transition settles the call once and publishes a call-bound parent resume.
The parent accepts only a completed child whose execution content and decrypted
state match those stored digests.

Allow authenticated cancellation only while a supported workflow is queued or
waiting. The same transaction cancels pending waits and subworkflow calls, wakes
children to observe the terminal parent call, clears the lease, and publishes a
worker finalization event. The worker settles related evaluation/replay lineage and
signs evidence. Running work is not cancelled because it may be between durable
boundaries. External-effect node types remain non-executable, so this cancellation
slice cannot race a supported side effect.

## Consequences

- Child and parent executions remain independently inspectable and tenant-bound.
- Raw state crosses the boundary only through encrypted database records; APIs,
  traces, audits, and evidence expose digests.
- Suspended children observe cancellation at an immediate durable wake rather than
  waiting for their original timer.
- Parallel subworkflow calls and external agent/tool execution remain outside this
  phase until the scheduler has explicit branch tokens and the governed-effect path
  is integrated.
