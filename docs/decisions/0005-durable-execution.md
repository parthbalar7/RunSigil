# ADR 0005: Durable intent and reconciliation

- Status: accepted
- Date: 2026-08-31

Intent, action, policy decision, budget reservation, approval requirement, trace,
audit, and outbox event commit before dispatch. The worker commits `executing` and
its lease before external I/O. A stable action idempotency key crosses retries.

Timeouts, lost receipts, and indeterminate provider responses become
`reconciliation_required`. They are never blindly replayed or labeled success.
Exactly-once behavior is not claimed.

