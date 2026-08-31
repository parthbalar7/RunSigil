# ADR 0006: Stateless protocol ingress over durable runs

- Status: accepted
- Date: 2026-08-31

RunSigil exposes MCP `2026-07-28` Streamable HTTP and the A2A `1.0` JSON-RPC
binding as stateless protocol facades in the gateway. A protocol task identifier is
the existing durable Run UUID; the gateway owns no task database and does not invent
a second source of execution truth.

The caller's bearer credential is forwarded only to the control API. The control API
authenticates it, derives the organization, installs RLS context, checks scopes, and
owns all Run mutations. Protocol payloads cannot supply an organization identifier.
The gateway never forwards that bearer credential to the worker or an external
provider.

MCP validates the mirrored protocol-version, method, and tool-name headers against
the body. It supports `server/discover`, one governed tool, and the stable Tasks
extension's get, update, and cancel operations. Task input updates can satisfy the
existing exact-content approval; they do not create a weaker approval mechanism.

A2A accepts only one structured `data` Part for the implemented skill. Text, raw
bytes, and URL Parts are rejected, so this slice does not become a general prompt or
file ingestion path. The public Agent Card declares JSON-RPC 1.0 and truthfully marks
streaming, push notifications, and extended cards unsupported.

Cancellation is allowed only while the Run is waiting for approval and the action is
still `proposed`. This boundary is before outbox dispatch and external I/O. Queued,
running, completed, failed, and ambiguous Runs fail cancellation closed until a later
fenced cancellation design exists.

This decision implements the protocol-ingress part of Milestone 2. OpenTelemetry
GenAI conventions, expanded budget scopes, DLQ tooling, and framework adapters remain
separate work and are not implied by these endpoints.
