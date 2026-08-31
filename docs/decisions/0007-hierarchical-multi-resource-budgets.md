# ADR 0007: Hierarchical multi-resource budgets

## Status

Accepted for Milestone 2.

## Decision

Represent a budget target as a tenant-owned `BudgetScope` with one explicit target:
organization, project, environment, agent, user, or model route. Each `Budget` belongs
to one scope and one resource key: `currency:USD`, `tokens`, `requests`,
`concurrent_runs`, `tool_actions`, or `model_calls`. Compound foreign keys preserve
tenant identity for every target.

Before an action is executable, resolve every applicable scope and lock matching
budget rows in UUID order. Every requested resource must have active coverage. Check
all limits before modifying any row, then create all reservations in the same
transaction as the intent, decision, approval requirement, action, and outbox. Link
the action to every reservation while retaining one primary reservation identifier
for backward-compatible authorization lineage.

An unambiguous committed effect moves measured cumulative units from reserved to
spent; concurrency units are released. A failed effect releases its estimate. An
ambiguous effect keeps all reservations active until provider reconciliation becomes
unambiguous. Model integrations may supply actual token/model-call usage to the same
settlement function; the current demo tool has no model call and reserves only
currency, request, concurrency, and tool-action units.

## Consequences

Stable-order row locking makes the final quota unit concurrency-safe and avoids
cross-scope deadlock ordering. One action may create many reservation rows, which is
intentional evidence of every enforced limit. A missing scope/resource configuration
fails closed. Other currencies require explicit `currency:<ISO code>` configuration;
this slice seeds USD only and performs no foreign-exchange conversion.
