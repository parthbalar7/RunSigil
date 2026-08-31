# ADR 0008: Bounded ambiguous-effect dead letters

## Status

Accepted for Milestone 2.

## Decision

Never retry execution after an ambiguous provider outcome. Use the provider's receipt
lookup endpoint with the original idempotency key. Count reconciliation attempts in
both lifetime and current-redrive-cycle fields. When a cycle reaches the configured
bound without an answer, atomically move the action and run to `dead_lettered` and
create or reopen one tenant-owned `DeadLetter` row. Keep its budget reservations
active because the effect may exist.

Expose scoped list and redrive APIs plus matching CLI commands. Redrive requires the
current dead-letter version, an authenticated operator reason, an open status, no
lease, intact content lineage, and remaining redrive allowance. It moves the action
only to `reconciliation_required`, resets the cycle counter, and writes trace and
hash-chained audit events. It never creates an `action.ready` event and therefore
cannot execute the uncertain effect again.

## Consequences

Operators get a durable, inspectable terminal instead of an infinite retry loop.
Concurrent or replayed redrives fail on state/version checks. PostgreSQL polling is
adequate for this reference slice, but this is not a high-volume broker DLQ claim.
Long-lived ambiguous reservations require an explicit future adjudication workflow;
silently releasing them would permit overspend.
