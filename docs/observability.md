# Observability

Set `RUNSIGIL_OTEL_ENABLED=true` and
`RUNSIGIL_OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318`. Each service exports
OTLP/HTTP traces to `/v1/traces` and metrics to `/v1/metrics`. Local Compose enables
this against its isolated collector and debug exporter.

The gateway emits `execute_tool demo.invoice.send` with
`gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, run/action IDs, and
`gen_ai.execute_tool.duration`. The adapter helper emits `invoke_agent <name>` and
`gen_ai.invoke_agent.duration`. Policy, approval, and budget operations use RunSigil
namespaced spans and duration metrics. Workflow advancement emits one
`runsigil.workflow.node` operation per durable node attempt plus the
`runsigil.workflow.node.duration` metric. Attributes contain correlation IDs, node
type, and outcome; workflow state and evaluation payloads are excluded.
Wait creation, resolution, expiry, and resume are represented by metadata-only
workflow trace events containing correlation IDs, type, status, and digests. Human
annotations emit safe audit metadata; response payloads and free-form review text
are not captured.
Subworkflow start/settlement, per-node policy decisions, cancellation, and replay
settlement add parent/child IDs, effects/reason codes, status, and state/path/content
digests only. Child state, scenario assertions, and replay state are never emitted.
Workflow tool nodes add parent-run request/settlement events containing only call,
child Run, action, tool, and content-digest identifiers. Provider execution remains
on the child Run's existing `execute_tool` span, so correlation does not require
copying arguments or receipts into telemetry.
Deterministic tool simulation emits profile/tool/argument/result digests and an
explicit false side-effect flag. Agent model egress emits a GenAI `chat` client
operation with run, model-call, and route/model identifiers plus token/cost
settlement metadata; request and output content remain excluded.

Raw prompts, model outputs, tool arguments, recipient addresses, approval reasons,
and credentials are intentionally absent. Enabling OTLP does not enable content
capture. OpenTelemetry GenAI semantic conventions are development-stability; pin and
review convention changes during upgrades.
