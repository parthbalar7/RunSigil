# ADR 0004: Scoped API key and workload identity first

- Status: accepted
- Date: 2026-08-31

The first slice uses high-entropy API keys stored as SHA-256 digests and explicit
scopes. The API derives organization and actor identity from the key. The worker and
gateway use separate service credentials; the gateway mints a short-lived,
audience-bound token for the provider.

OIDC validation, OAuth token exchange, human-to-agent delegation chains, DPoP/mTLS,
SAML, SCIM, and SPIFFE remain unimplemented and are not implied by this slice.

