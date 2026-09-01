# ADR 0015: Explicit simulation and durable agent model calls

- Status: accepted
- Date: 2026-09-01

## Context

Milestone 3 phase 5 could execute the fixed transactional workflow tool, but fork,
replay, and evaluation rejected every effectful graph. The typed `agent` node was
also modeled but not executable. Enabling either capability without exact lineage
would risk duplicating a production effect, leaking model content, bypassing budgets,
or blindly retrying an ambiguous provider call.

## Decision

Effectful fork, replay, and evaluation require an explicitly selected immutable
simulation profile. The profile binds organization, project, tool and tool digest,
provider, and contract version. The worker validates the ordinary encrypted-state
tool contract and stores an append-only completed simulation call binding arguments,
tool, profile, result, execution, node, and sequence. Its deterministic receipt
states that no side effect was performed. Live workflow starts cannot select this
mode implicitly.

A serial `agent` node may reference only the active fixed demo model route and an
active policy bundle in the same project. Before provider I/O, the worker persists
an allow decision, delegation, multi-scope budget reservations, exact request and
route digests, encrypted request, stable idempotency key, model call, ready outbox,
and timeout outbox. A committed claim carries a hashed one-time token and lease. The
gateway revalidates the claim, policy, delegation, route, workload identity, request
digest, and active reservations online before minting a short-lived audience-bound
credential containing `model_call_id` rather than an action claim.

Completed model output is encrypted with model-call/content-bound associated data.
Only output digest, provider reference, token counts, cost, states, and identifiers
appear in APIs, telemetry, audits, UI, and evidence. A lost or ambiguous response
enters reconciliation by the same provider idempotency key; it is never executed
again blindly. Unresolved calls conservatively settle their reservations at the
configured bound and fail the workflow.

Agent/tool mixing, parallel agent calls, agent nodes in referenced subworkflows, and
cancellation after model-call queueing are rejected until descendant external-call
cancellation can be fenced.

## Consequences

- Tests and evaluations cannot accidentally perform the supported transactional
  effect when an exact profile is supplied.
- Simulation and model-call records carry forced RLS and compound tenant lineage.
- The fixed demo route proves the durability and security boundary without claiming
  arbitrary provider support or general agent hosting.
- General simulation providers, production model connectors, parallel external
  calls, supervisor nodes, and handoffs remain later work.
