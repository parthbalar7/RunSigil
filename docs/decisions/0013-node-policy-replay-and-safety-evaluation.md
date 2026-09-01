# ADR 0013: Node policy, deterministic replay, and safety evaluation

## Status

Accepted for Milestone 3 phase 4.

## Decision

Permit any executable workflow node to reference an active policy bundle in the
workflow project. Before node behavior, the worker evaluates the existing typed
policy engine with action `workflow.node.execute` and a node-type resource. It
persists an append-only `WorkflowPolicyDecision` binding execution, Run, node,
sequence, evaluation number, input and policy digests, effect, reason, and expiry.
Only `allow` advances. Missing, disabled, invalid, expired, digest-mismatched, or
non-allow decisions fail the execution closed. A still-current decision can be
reused after a durable suspension; a changed or expired decision requires a new
evaluation record.

Implement replay as a new execution from an explicit non-terminal checkpoint of a
completed source Run. `WorkflowReplay` binds source/replay execution and Run IDs,
the source checkpoint, source final state digest, source full-path digest, and its
own immutable content digest. Worker settlement records replay final digests and
returns `matched` only when both equal the source; otherwise it returns `diverged`.
Replay does not claim simulation of external models or tools.

Encrypt scenario policy/safety assertions with the existing dataset payload. The
policy grader requires a persisted `allow` decision for every declared required
node. The safety grader requires successful execution, no declared forbidden node
in the path, and compliance with the declared maximum step count. These join task,
trajectory, and deterministic-environment graders with equal weight. Results expose
outcomes and digests, never scenario content.

## Consequences

- Policy history and replay comparison become signed workflow evidence rather than
  inferred trace metadata.
- A replay can be reproducible or explicitly divergent; it is never silently called
  equivalent.
- Policy and safety release gates remain deterministic and auditable.
- Provider simulation, model-based grading, dynamic handoff policy, sampling, and
  production grader operations remain later work.
