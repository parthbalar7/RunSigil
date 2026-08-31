# ADR 0002: Fail-closed, content-bound governance

- Status: accepted
- Date: 2026-08-31

Protected work proceeds only with a valid policy decision, current content digest,
active budget reservation, and—when required—a one-use approval. Missing,
unavailable, ambiguous, expired, or mismatched evidence denies execution.

Approvals cannot edit or rebind arguments. A changed request is a new intent and
requires a new policy decision and approval. This is stricter than convenience
workflows that mutate approved payloads and makes authorization evidence portable.

