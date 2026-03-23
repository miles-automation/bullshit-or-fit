# AGENTS.md - Bullshit or Fit

## Scope

This repo hosts the public landing page + lead capture flow for bullshitorfit.com.

## Required health endpoints

- `GET /healthz`
- `GET /api/v1/healthz`

## Leads integration contract

Use Spark Swarm public leads endpoints via backend proxy routes:

- `GET /api/v1/landing-config` -> Spark Swarm `/api/v1/public/sparks/bullshit-or-fit/landing-config`
- `POST /api/v1/leads/submit` -> Spark Swarm `/api/v1/public/sparks/bullshit-or-fit/leads`
- `POST /api/v1/leads/resend` -> Spark Swarm `/api/v1/public/sparks/bullshit-or-fit/leads/resend-confirmation`
- `GET /api/v1/leads/confirm?token=...` -> Spark Swarm `/api/v1/public/leads/confirm`

## Commands

- `make install`
- `make check`
- `make build`

## Style

- Keep copy concise and conversion-focused.
- Preserve legal disclaimer that output is screening assistance, not final hiring decision.
