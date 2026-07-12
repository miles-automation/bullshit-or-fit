# CLAUDE.md - Bullshit or Fit

## Product intent

A market-intelligence toolkit for **job seekers**: know where the work is, what
your skills are worth, and where demand is heading — so your next move is
informed, not a guess. Live surfaces: `/local` (commute-shed employer radar),
`/you` (personal market fit), `/trends` (national hiring trends).

> The employer-side "verify a candidate's resume" play was **killed 2026-07-09**:
> resolving claimed employers to real websites false-accuses real people (unresolvable
> ≠ fake; funded companies have placeholder sites; common names are ambiguous). An
> accusation product with that false-positive profile is a liability. The verification
> instinct was inverted into a job-seeker "is your résumé substance or filler?" self-check
> (see the feature spec). Don't reintroduce candidate-screening framing.

## Delivery rules

- Keep the funnel simple: value proposition -> live tools -> waitlist (email capture).
- Keep forms resilient: show success, rate-limit, and failure states.
- Disclaim clearly: this is market intelligence, **not career or financial advice**.

## Deploy

Use platform workspace rollout command:

```bash
./bin/platform prod rollout bullshit-or-fit --tag sha-<commit> --yes --apply-secrets
```
