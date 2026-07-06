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
"Who is hiring?" monthly threads (HN Algolia API) and stores **raw** post text in
Postgres — its own `jobtrends` schema inside the `bullshit_or_fit` database. This
round is ingestion only; analysis reconstructs everything from the raw rows later.

- `backend/app/jobtrends/` — `hn_algolia.py` (API client), `ingest.py` (parse +
  idempotent upsert), `worker.py` (looping ingest container), `cli.py`, `models.py`.
- Tables: `jobtrends.hn_hiring_threads` (one row/monthly thread) and
  `jobtrends.hn_hiring_posts` (one row/job post, `raw_text` verbatim). Idempotent
  on the HN ids — re-running a month upserts, never duplicates.
- Runtime: the `bullshit-or-fit-ingest` compose worker runs `alembic upgrade head`
  on boot, backfills ~18 months, then re-ingests the trailing months daily. The
  landing/lead web app stays DB-free.

```bash
make migrate                      # alembic upgrade head
make jobtrends-ingest             # one-shot backfill (idempotent); MONTHS=6 to override
make new-migration MSG="..."      # scaffold a sequential migration
```
