# ADR 0003: PostgreSQL tenancy and RLS

- Status: accepted
- Date: 2026-08-31

PostgreSQL is the system of record. Every tenant-owned row contains
`organization_id`; compound foreign keys prevent cross-tenant graph edges. Forced
RLS compares rows to transaction-local authenticated context. Redis is notification
and leasing infrastructure only, never the sole record.

Authentication uses a narrow database function so the runtime role can resolve a
hashed API key without receiving unrestricted access to tenant rows. The schema
owner is confined to migrations.

