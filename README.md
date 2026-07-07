# Bullshit or Fit

Landing page + lead capture for Bullshit or Fit.

## Stack

- FastAPI backend serving built React SPA
- React + Vite frontend
- Spark Swarm public leads API integration

## Local Development

```bash
make install
make dev-backend
make dev-frontend
```

Backend health endpoints:

- `GET /healthz`
- `GET /api/v1/healthz`

## Checks

```bash
make check
```

## Build

```bash
docker build --platform linux/amd64 -t ghcr.io/miles-automation/bullshit-or-fit:latest .
```

## Deploy

```bash
./bin/platform prod rollout bullshit-or-fit --tag sha-<commit> --yes --apply-secrets
```

## jobtrends data engine

The hiring-market data engine behind Bullshit or Fit. It ingests Hacker News
"Who is hiring?" monthly threads (HN Algolia API), stores **raw** post text in
Postgres (its own `jobtrends` schema inside the `bullshit_or_fit` database), and
derives analysis tables from it. Raw is immutable; every derived table is fully
rebuildable, so taxonomy/comp logic can evolve without re-fetching.

- Ingest: `hn_algolia.py` (API client), `ingest.py` (parse + idempotent upsert).
  Raw tables `jobtrends.hn_hiring_threads` / `hn_hiring_posts` (`raw_text` verbatim,
  keyed on HN ids → re-running a month upserts, never duplicates). Each row is
  tagged with `source` (`hn`) + `stream` — the multi-source spine. Streams today:
  `hiring` (jobs/demand) and `wants_hired` (candidates/supply).
- Analysis (derived, rebuilt from raw): `taxonomy.py` + `extract.py` (keyword
  presence → `keyword_month_stats`), `comp.py` (precision-first salary parsing →
  `post_comp`), `recurrence.py` (author cohorts/churn → `cohort_month`),
  `market.py` (per-stream volume → `stream_month`). The keyword/comp/churn tables
  are scoped to the `hiring` stream.
- Reports: `trend.py` (keyword share-of-postings + MoM), comp coverage/quartiles,
  monthly churn, and demand/supply (job-seekers per opening). Exposed via the CLI below.
- Runtime: the `bullshit-or-fit-ingest` compose worker runs `alembic upgrade head`
  on boot, backfills ~18 months, then each day re-ingests the trailing months and
  rebuilds the derived tables. The landing/lead web app stays DB-free.

```bash
make migrate                      # alembic upgrade head
make jobtrends-ingest             # one-shot backfill (idempotent); MONTHS=6 to override
make new-migration MSG="..."      # scaffold a sequential migration

# analysis (backend/, after ingest):
uv run python -m app.jobtrends.cli extract              # rebuild all derived tables
uv run python -m app.jobtrends.cli trend python rust mcp # keyword share-of-postings
uv run python -m app.jobtrends.cli comp                 # salary coverage + quartiles
uv run python -m app.jobtrends.cli churn                # author recurrence + churn
uv run python -m app.jobtrends.cli market               # demand/supply per month
```
