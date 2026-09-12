# Source freshness

The ingest worker checks the newest stored data after each ingestion cycle. Continuous
boards become stale after 48 hours; monthly HN threads after 40 days. Sources that
have never produced data remain silent. This detects regressions rather than
claiming that an unconfigured source is broken.

The worker resolves its configured Spark by slug, then posts a scoped event with
JSON-encoded metadata. BULLSHIT_OR_FIT_SS_API_KEY must authorize that Spark. A
stale transition records an incident; recovery records a status change.

Only accepted notifications advance the persisted transition state. Failed requests
or missing credentials are retried on later worker cycles. A process failure after
HTTP acceptance but before database commit can produce a duplicate event. No
notification failure interrupts ingestion.

Migration 0016 introduces SEC evidence tables; 0017 introduces source_health.
Both upgrade and downgrade paths were verified on isolated PostgreSQL 16, including
a full upgrade from an empty database and a 0017 → 0015 → 0017 cycle. SEC collection
remains opt-in through SEC_ENABLED and requires a contact email.
