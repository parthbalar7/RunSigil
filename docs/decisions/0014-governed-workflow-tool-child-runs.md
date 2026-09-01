# ADR 0014: Governed workflow tools use child Runs

## Status

Accepted for Milestone 3 phase 5.

## Decision

Execute the first effectful Workflow Engine `tool` node by creating a separate
governed-action child Run rather than dispatching provider I/O from the workflow
engine. The node references the supported catalog tool, an encrypted-state
arguments key, and a safe-result key. It cannot contain arguments, credentials,
prompts, or outputs in its definition.

In one worker transaction, persist the child Run, exact Intent, delegation, typed
action policy decision, hierarchical budget reservations, optional content-bound
approval, encrypted Action, action outbox, immutable `WorkflowToolCall`, timeout
outbox, traces, and audit rows before suspending the parent. The existing action
worker commits its claim before gateway I/O and uses stable provider idempotency and
receipt reconciliation. Successful settlement signs child evidence and publishes a
call-bound parent wake. The parent verifies intent, action, safe-result, and child
evidence digests before completing the node.

A node timeout may cancel only a pending approval or an approved action whose
outbox has not been claimed. Once execution could have reached the provider, the
parent remains suspended through reconciliation and a possible DLQ/redrive cycle.
Operator redrive remains reconcile-only. Workflow cancellation is accepted only at
the same pre-effect boundary.

Reject parallel tool suspension, tool nodes inside referenced subworkflows,
checkpoint forks, replay, and evaluation for effectful deployments. Those paths
require a provider simulation contract and additional cancellation fencing before
they can be safe.

## Consequences

- Workflow tools inherit the already-tested policy, approval, budget, gateway,
  credential, reconciliation, and evidence boundary.
- Parent and child lifecycles remain independently inspectable and cryptographically
  linked without copying raw arguments into workflow metadata.
- No general connector or arbitrary agent-process executor is implied.
- Provider simulation, nested/parallel effects, agent and supervisor nodes, and
  dynamic handoffs remain later Milestone 3 work.
