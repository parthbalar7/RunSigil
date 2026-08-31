# ADR 0009: Privacy-safe telemetry and framework adapters

## Status

Accepted for Milestone 2.

## Decision

Emit OTLP traces and metrics from the control API, worker, gateway, and adapter helper.
Use the OpenTelemetry GenAI operation names `invoke_agent` and `execute_tool` and the
corresponding duration metric names. Add RunSigil run/action correlation identifiers,
decision metadata, and an explicit `content_captured=false` attribute. Do not add raw
prompts, outputs, tool arguments, approval reasons, recipient addresses, or secret
values. Durable trace rows reuse an active OpenTelemetry trace/span identifier when
available and use deterministic run correlation otherwise.

Define a small Python adapter contract consisting of settings, a typed HTTP client,
safe result projection, a manifest, and an optional agent-invocation telemetry context.
The LangGraph adapter is a graph node that uses the framework's checkpointed
`interrupt` and validates the exact digest supplied on resume. The OpenAI Agents
adapter is a typed function tool with `needs_approval=True`; once the SDK resumes that
exact tool call, it creates the RunSigil run and bridges that authenticated approval
to the returned content digest. Its idempotency key derives from the SDK tool-call ID.

## Consequences

Adapters reuse the same API, policy, budget, worker, gateway, and evidence path and do
not become alternate executors. They require their pinned optional framework extras.
No OpenAI API key is required for adapter contract tests. The GenAI semantic
conventions are development-stability upstream, so emitted names may need future
compatibility revisions without weakening the privacy boundary.
