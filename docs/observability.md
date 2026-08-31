# Observability

Set `RUNSIGIL_OTEL_ENABLED=true` and
`RUNSIGIL_OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318`. Each service exports
OTLP/HTTP traces to `/v1/traces` and metrics to `/v1/metrics`. Local Compose enables
this against its isolated collector and debug exporter.

The gateway emits `execute_tool demo.invoice.send` with
`gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, run/action IDs, and
`gen_ai.execute_tool.duration`. The adapter helper emits `invoke_agent <name>` and
`gen_ai.invoke_agent.duration`. Policy, approval, and budget operations use RunSigil
namespaced spans and duration metrics.

Raw prompts, model outputs, tool arguments, recipient addresses, approval reasons,
and credentials are intentionally absent. Enabling OTLP does not enable content
capture. OpenTelemetry GenAI semantic conventions are development-stability; pin and
review convention changes during upgrades.
