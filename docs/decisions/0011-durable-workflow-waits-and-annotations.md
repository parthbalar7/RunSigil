# ADR 0011: Durable workflow waits and human annotations

## Status

Accepted for Milestone 3 phase 2.

## Decision

Execute timer, approval, request-information, and event nodes as durable serial
waits. Reaching one atomically persists a `WorkflowWait`, exact content and state
digests, safe request metadata, expiry, trace/audit rows, and a scheduled outbox
wake before suspending the execution. Timers expire into the successful `elapsed`
resolution; every other unresolved wait expires into a fail-closed workflow.

Approval, information, and event APIs derive tenant and actor identity from the
authenticated context. A response must name the exact wait digest. Approval chooses
an `approved` or `denied` branch. Event responses must also match the configured
event key. Information and event documents are encrypted using content-bound
associated data; APIs, traces, audits, and evidence expose only digests and safe
metadata.

The response transaction locks the scheduled wake before the wait. It verifies that
the wait is pending and unexpired, stores one terminal resolution, cancels the old
wake, and appends an immediate resume event. The worker independently verifies wait,
execution, node, sequence, type, state, and response digests before recording a node
attempt and checkpoint. Database triggers make immutable request lineage and a
resolved wait single use.

Definitions containing both parallel fan-out and a phase-two wait are rejected.
This avoids claiming correct branch-local suspension before the scheduler has an
explicit token model for parallel waits.

Authenticated reviewers may append idempotent evaluation annotations with an exact
result/scenario/run lineage, constrained label, optional bounded score, and safe
reason codes. Annotations are immutable and contain no free-form content. They are
audited, but they do not alter release gates or previously signed run evidence.

## Consequences

- PostgreSQL and its transactional outbox remain authoritative for suspension,
  wake-up, timeout, and response/timeout race ordering.
- A workflow can wait across worker restarts without retaining an in-memory task.
- Scoped API-key authentication is the current event sender boundary; stronger
  source-specific signatures and replay windows remain production work.
- Agent, supervisor, tool, handoff, and subworkflow execution remain outside this
  phase, as do distributed parallel waits and annotation consensus.
