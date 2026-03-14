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
