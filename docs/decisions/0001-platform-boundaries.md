# ADR 0001: Independent control-plane boundaries

- Status: accepted
- Date: 2026-08-31

RunSigil is a new platform, not a fork or runtime extension of the architectural
reference. It owns its packages, database, environment variables, images, cluster,
namespace, APIs, and deployment resources. The first delivery implements only a
governed-action slice and does not create placeholder services for future features.

This keeps trust claims reviewable and prevents accidental coupling to another
deployment. Later capability additions require independent contracts and tests.

