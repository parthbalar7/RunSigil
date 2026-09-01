# Workflow Engine v2

## Current phase

Milestone 3 phases 6 and 7 extend the durable workflow foundation. It is a
real executor for these node types:

- `input` and `output`;
- `condition` with `eq`, `ne`, `gt`, `gte`, `lt`, or `lte`;
- `parallel` fan-out;
- `join` deterministic fan-in;
- `bounded_loop`;
- `timer` with a durable scheduled wake;
- `approval` with exact `approved` and `denied` branches;
- `request_information` with an encrypted response inserted at a declared state key;
- `event` with an authenticated event key and encrypted payload inserted at a
  declared state key;
- serial `subworkflow` calls to exact deployed workflow versions;
- serial `tool` nodes for the catalog-bound `demo.invoice.send` transactional tool;
- serial `agent` nodes for the catalog-bound `demo-governed-model` route.

The typed contract also recognizes later supervisor and handoff node types, but
deployment rejects them. RunSigil never saves an unsupported construct as executable.

## Definition safety

Each definition requires global maximum steps, duration, tokens, and cost. Every
cycle must traverse a bounded loop with its own positive iteration, duration, token,
and cost limits. Validation also checks unique node and edge IDs, references,
reachability, entry/output rules, condition branches, fan-out shape, and fan-in
shape.

Definitions may contain identifiers and safe control values. Raw prompts, model
outputs, action arguments, credentials, and secrets cannot be embedded in node
configuration. Tool and agent nodes use catalog UUIDs and declared encrypted-state
input/result keys; arguments and model inputs never appear in the definition.

Validate locally:

```powershell
runsigil workflow validate workflow.json --json
```

Create and deploy:

```powershell
runsigil workflow create workflow.json --slug review-flow --name "Review flow" --json
runsigil workflow deploy <version-id> --json
runsigil workflow run <deployment-id> input.json --json
runsigil workflow simulation-profile-create <tool-id> --name deterministic-v1 --json
runsigil workflow fork <run-id> <checkpoint-id> --simulation-profile-id <profile-id> --json
```

Inspect and resolve a pending wait with its exact returned content digest:

```powershell
runsigil workflow wait-get <wait-id> --json
runsigil workflow wait-approve <wait-id> --digest <content-digest> --json
runsigil workflow wait-deny <wait-id> --digest <content-digest> --json
runsigil workflow wait-information <wait-id> information.json --digest <content-digest> --json
runsigil workflow wait-event <wait-id> <event-key> payload.json --digest <content-digest> --json
```

## Durability

Starting a workflow atomically persists the Run, execution, encrypted state,
initial checkpoint, trace, audit, and transactional outbox event. The worker:

1. commits a version-fenced lease;
2. verifies the immutable definition digest and encrypted state digest;
3. executes one deterministic node;
4. appends an attempt and encrypted checkpoint;
5. publishes the next step or completes the Run;
6. signs workflow evidence.

An expired lease can be recovered. A stale claim token cannot complete the node.
Step and deadline exhaustion fail the workflow closed.

On first reaching a wait node, the worker atomically persists the tenant-bound wait,
its state and request digests, safe metadata, expiry, trace/audit rows, and scheduled
wake event, then clears its lease. No node attempt or checkpoint is recorded until
the wait resolves. A response locks both the scheduled event and wait, verifies the
type, exact content digest, expiry, and event key where applicable, stores only an
encrypted response plus digest, and publishes a new wake. The worker re-verifies
that lineage before advancing. A response/timeout race has one terminal winner, and
expired waits fail the run closed.

At a tool node the worker creates a separate governed child Run. Exact intent,
delegation, action policy decision, all applicable budget reservations, optional
one-use approval, encrypted arguments, Action, immutable workflow-tool call, and
both outbox paths commit before any effect. The parent suspends while the existing
action worker and gateway execute or reconcile the child. Terminal success resumes
the parent only after the action/intent/result and child evidence digests verify.
The API and UI expose identifiers, safe previews, states, and digests—not the tool
arguments.

Tool timeouts are fenced against the action claim. A pending approval or unclaimed
action can be cancelled before effect; executing or ambiguous actions remain
suspended until receipt reconciliation. Unknown outcomes may enter the durable DLQ,
and bounded operator redrive continues reconciliation only before the parent can
resume.

Effectful fork, replay, and evaluation require an explicit simulation profile. A
profile is immutable and binds project, tool digest, deterministic provider, and
contract version. The simulation executor validates the same encrypted-state tool
input contract, writes an append-only call with exact digests, returns only a safe
deterministic receipt, and records `side_effect_performed=false`. It never creates a
child action or contacts the effect provider. Omitting or mismatching the profile
fails before a new effectful execution is created.

At an agent node the worker persists an encrypted request, exact request and model-
route digests, allow decision, delegation, all model-route budget reservations,
stable idempotency key, call record, ready outbox, and timeout outbox before egress.
It commits a claim and lease before the gateway revalidates every lineage element
and mints a short-lived model-call-specific provider token. Outputs are encrypted;
API, traces, audits, UI, and signed evidence expose only digests, usage, cost, and
safe status. An ambiguous result is reconciled by idempotency key and never blindly
executed again.

Checkpoint forks use `POST /v1/workflow-runs/{run_id}/forks`. Fork lineage points to
the exact source checkpoint and receives a new Run, execution digest, encrypted
state binding, audit event, and outbox sequence.

Referenced subworkflows use a deployment UUID and a safe `result_state_key`. Parent
deployment walks every reference recursively, verifies active same-scope deployment
and definition digests, rejects cycles, and limits nesting to eight levels. At the
node, the worker atomically persists the child Run/execution, immutable parent-child
call, timeout wake, traces, audit, and both outbox paths before suspending. Child
completion publishes a call-bound resume. The parent decrypts and verifies the child
state and stores it only under the declared key. Failure, cancellation, expiry, or
lineage mismatch fails closed.

Any node may specify `policy_bundle_id`. The worker evaluates a typed
`workflow.node.execute` context before the node performs work. Its append-only record
binds the node/sequence, policy and input digests, exact effect/reason, and expiry.
Only `allow` advances. Missing, inactive, corrupt, expired, or non-allow policy fails
the run and is visible through traces and signed evidence without raw state.

Replay uses `POST /v1/workflow-runs/{run_id}/replays` or:

```powershell
runsigil run replay <run-id> <checkpoint-id> --json
runsigil run cancel <queued-or-waiting-run-id> --json
```

The source must already be completed and the selected checkpoint must have remaining
work. A new execution starts from the encrypted checkpoint and an immutable replay
record binds source/replay executions, source checkpoint, final source state digest,
and final source path digest. Settlement is `matched` only if both replay digests
equal the source; otherwise it is explicitly `diverged`.

Queued or waiting workflows can be cancelled through the existing run cancellation
endpoint. Pending waits and subworkflow calls settle once, child workflows receive a
durable cancellation wake, and a worker finalization event signs terminal evidence.
A workflow waiting on a tool may be cancelled only while its exact child approval is
still pending and no earlier tool effect exists. After approval makes dispatch
possible, cancellation fails closed and reconciliation owns the outcome.
A workflow cannot be cancelled once an agent model call is queued; this prevents
orphaning external-call ownership while execution or reconciliation may occur.

## Phase boundary

Supervisor and handoff nodes are not executable yet. Tool execution and simulation
are limited to one governed transactional contract; agent execution is limited to
one fixed demo model route. The engine rejects parallel external nodes, agent/tool
mixing, and referenced subworkflows containing either external node until descendant
cancellation is fenced. Distributed branch suspension, general connectors,
arbitrary model providers, arbitrary state transforms, dynamic policy-constrained
handoffs, and the interactive editor are not claimed.
