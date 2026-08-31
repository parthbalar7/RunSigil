# MCP and A2A protocol ingress

This is the first completed vertical slice of Milestone 2. Both protocols create and
observe the same governed Run used by the REST API, worker, approval flow, gateway
authorization, provider, and evidence exporter.

## Authentication and tenancy

Protected requests require `Authorization: Bearer <scoped RunSigil API key>`. The
gateway passes the header only to the control API. The organization is derived from
that authenticated key and is never accepted in MCP arguments or A2A Parts. Required
scopes depend on the operation:

| Operation | Control API scope |
| --- | --- |
| Discover/list capabilities | `context:read` |
| Start a governed Run | `run:write` |
| Get/list tasks | `run:read` |
| Approve or deny task input | `approval:decide` |
| Cancel before approval | `run:write` |

Cross-organization Run identifiers return the same not-found result as unknown IDs.
Responses contain digests, identifiers, status, redacted previews, and redacted
receipts; raw action arguments are not returned.

## MCP 2026-07-28

The Streamable HTTP endpoint is `POST /mcp`. Every request is independent and must
carry:

- `Content-Type: application/json`
- `Accept: application/json, text/event-stream`
- `MCP-Protocol-Version: 2026-07-28`
- `Mcp-Method` equal to the JSON-RPC body method
- `Mcp-Name` equal to `params.name` for `tools/call`
- per-request protocol version, client identity, and capabilities in `params._meta`

The server implements `server/discover`, `tools/list`, `tools/call`, `tasks/get`,
`tasks/update`, and `tasks/cancel`. It exposes one tool,
`runsigil.governed_action.start`. Because calls return durable task handles, clients
must opt into `io.modelcontextprotocol/tasks` in each applicable request.

`tasks/update` accepts the `approval` input response returned by `tasks/get`. An
accepted response must contain the exact `content_digest`, an `approve` or `deny`
decision, and a reason. Declining or cancelling the elicitation safely denies the
pending approval. `tasks/cancel` succeeds only at the pre-effect approval boundary.

The endpoint has no initialize handshake, GET stream, protocol session, or
`Mcp-Session-Id`. Origins are checked when an `Origin` header is present, and mirrored
header/body mismatches return MCP `HeaderMismatch` (`-32020`) before dispatch.

## A2A 1.0

The public Agent Card is `GET /.well-known/agent-card.json`; its preferred interface
is `POST /a2a/rpc` with the JSON-RPC binding and `A2A-Version: 1.0`.

The implemented core methods are `SendMessage`, `GetTask`, `ListTasks`, and
`CancelTask`. Optional streaming, subscriptions, push notification configuration,
and extended Agent Cards return the standard unsupported-operation error and are
declared false in the Agent Card.

`SendMessage` requires `ROLE_USER` and exactly one structured `data` Part. A new task
uses this application payload shape:

```json
{
  "operation": "runsigil.governed_action.start",
  "project_id": "<uuid>",
  "environment_id": "<uuid>",
  "agent_id": "<uuid>",
  "recipient": "ops@example.test",
  "amount_cents": 4200,
  "description": "Invoice notification",
  "idempotency_key": "caller-stable-key"
}
```

When the task reports `TASK_STATE_INPUT_REQUIRED`, a follow-up `SendMessage` on the
same `taskId` and `contextId` uses:

```json
{
  "operation": "runsigil.approval.decision",
  "content_digest": "sha256:<64 lowercase hex characters>",
  "decision": "approve",
  "reason": "Reviewed exact content"
}
```

A2A text, raw, and URL Parts are intentionally unsupported. Task history is omitted
because this slice does not persist raw protocol messages. Completed tasks return a
structured artifact containing only the safe Run result.

## State mapping

| RunSigil Run | MCP Task | A2A Task |
| --- | --- | --- |
| `authorizing`, `queued` | `working` | `TASK_STATE_SUBMITTED` |
| `running`, `reconciliation_required` | `working` | `TASK_STATE_WORKING` |
| `waiting_for_approval` | `input_required` | `TASK_STATE_INPUT_REQUIRED` |
| `completed` | `completed` | `TASK_STATE_COMPLETED` |
| `failed` | `failed` | `TASK_STATE_FAILED` |
| caller cancellation | `cancelled` | `TASK_STATE_CANCELED` |
| approval denial | `cancelled` | `TASK_STATE_REJECTED` |

## Live proof

With the isolated local stack running, execute:

```powershell
./examples/protocol-gateway/run-live.ps1
```

The script discovers MCP, creates and approves one MCP task, creates and approves one
A2A task, cancels another A2A task before dispatch, waits for both approved effects,
and asserts that raw recipients never appear in protocol results.
