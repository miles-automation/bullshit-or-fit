# Agent morning job search

Tools that let an authorized agent run the daily fit list, read it without
production SSH, and turn it into a short, reviewable result. Nothing here
submits applications, sends mail or contacts anyone.

Personal records (application tracker, feedback, claim and answer banks,
packets) are never stored in this repository. The owner's private runbook
says where they live and which feed task to use.

## Pieces

| Command | Runs where | Needs |
|---|---|---|
| `python -m app.jobtrends.cli fit --days 1 --format json` | production ingest container | database |
| `python -m app.jobtrends.morning_cli publish FILE --task ID --author NAME` | anywhere | `SPARK_SWARM_API_KEY` with access to the feed task |
| `python -m app.jobtrends.morning_cli latest --task ID [--schema S] [--out FILE]` | anywhere | same key |
| `python -m app.jobtrends.morning_cli run --home DIR (--export FILE \| --feed-task ID)` | where the records live | records folder |
| `python -m app.jobtrends.morning_cli feedback --home DIR --kind owner\|agent ...` | where the records live | records folder |
| `python -m app.jobtrends.morning_cli mail-status --home DIR` | where the records live | mail watcher log |

Run the agent commands from `backend/` with
`PYTHONPATH=. uv run python -m app.jobtrends.morning_cli ...`.

## Schemas

All documents carry a `schema` field. Readers must reject unknown schemas.

- `bof.fit-export/v1`: `generated_at`, `window`, `filters`, `data_freshness[]`,
  `hidden_tracker_roles`, `active_application_companies[]`, `roles[]`. Each
  role has `rank`, `role_id` (stable `ats_jobs.id`), `company_id`
  (`co:<normalized name>`), `listing_url`, `official_url` (null when the
  company's own posting is unknown), `pay`, `fit_reasons[]`, `gaps[]`,
  `active_application_at_company` (informational; the tracker decides), and
  `posting_text` for the top ten. The order is the text list's order.
- `bof.morning/v1`: `shortlist[]` (at most three strong fits, one per
  company, or none), `best_match` (a `role_id` or null), `held[]` (active
  application at the company, or another of its roles is shortlisted),
  `suppressed[]` (tracker roles and owner skips), `not_strong`,
  `agent_suggestions[]` (shown, never applied), `rules`, `packet`.
- `bof.fit-feedback/v1`, one JSON object per line: `id`, `role_id`,
  `company_id`, `url` (optional; role decisions follow it across sources),
  `source`, `decision` (`skip_role`, `good_fit`, `skip_company`, `note`),
  `reason`, `author`, `created_at`, `evidence`.
  Owner records live in `owner-feedback.jsonl`, agent suggestions in
  `agent-suggestions.jsonl`; the file decides the kind, not a field.
- `bof.claim/v1`: `id`, `text`, `evidence`, `verification`
  (`verified`, `unverified`), `confidential`, `employer`, `tags`.
- `bof.answer/v1`: `id`, `kind` (`owner_writing`, `approved_final`,
  `sent`), `text`, `source`, `question`, `confidential`.
- Mail status items: `date`, `source_ref` (folder:uid), `company`, `status`,
  `kind` (`automated_receipt`, `automated_notice`, `possible_human_reply`).
- Published documents carry `kind` equal to `schema`; `latest` filters on it.

## Rules the tools enforce

- A role in the tracker is suppressed; a company with an active tracker
  status (drafting, submitted, replied, screen, interview, offer) is held,
  one active application per company.
- `skip_role` hides one role. Only `skip_company` hides a company.
- Owner feedback written by any author other than the owner must quote the
  owner's words in `evidence`, or it is refused. The CLI refuses the owner's
  author name. The tools cannot verify identity; the rule is a convention.
- Morning documents never include owner evidence.
- Agent suggestions never change the shortlist.
- A strong fit has an official posting URL, listed pay, and a listing on
  the company's own site.
- Feedback appends are idempotent and never rewrite existing lines.
  Packets are never overwritten once written.
- Packet drafts use only verified, non-confidential claims.

## Not done here

The production worker does not publish the export on a schedule. That
needs a feed-scoped key in production, a deploy, and the owner's approval.
