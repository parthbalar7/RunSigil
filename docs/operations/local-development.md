# Local development

The local stack is isolated by RunSigil names, network, database, and volumes. It
does not discover or operate any other cluster or Compose project.

1. Copy `.env.example` to `.env`.
2. Generate independent random values for every password/token and a base64-encoded
   32-byte Ed25519 seed.
3. Set `RUNSIGIL_BOOTSTRAP_API_KEY` to a new `rsk_dev_...` value.
4. Start `docker compose --env-file .env -p runsigil -f deploy/compose/compose.yaml up --build`.
5. Wait for `/ready` on ports 8000, 8080, and 8090.
6. Follow the live examples in `README.md`, run
   `examples/governed-action/run-live.ps1` for the REST/CLI flow, or run
   `examples/protocol-gateway/run-live.ps1` for MCP and A2A.

OpenTelemetry traces and metrics are sent to the isolated collector when
`RUNSIGIL_OTEL_ENABLED=true`. Its debug exporter is development-only. Use
`runsigil dlq list --json` to inspect unresolved actions; `runsigil dlq redrive`
requires the current row version and always schedules reconciliation, not execution.

The development endpoints are the control API at `http://localhost:8000`, protocol
and egress gateway at `http://localhost:8080`, public A2A Agent Card at
`http://localhost:8080/.well-known/agent-card.json`, MCP at
`http://localhost:8080/mcp`, and A2A JSON-RPC at
`http://localhost:8080/a2a/rpc`.

The bootstrap key is hashed during migration/seed and never returned by the API.
Changing it later requires the explicit development seed command; it is not silently
rotated. Never reuse these credentials outside the isolated development stack.

To stop the stack without deleting data:

```powershell
docker compose --env-file .env -p runsigil -f deploy/compose/compose.yaml down
```

Volume deletion is intentionally not part of the normal command. If you need a
clean environment, confirm the exact `runsigil_*` volumes before removing them.
