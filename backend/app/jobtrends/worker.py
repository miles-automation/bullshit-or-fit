"""jobtrends ingest worker — the fleet's looping-container pattern.

Runs as its own container (see platform-infra/docker-compose.yml
`bullshit-or-fit-ingest`), mirroring lead-scheduler / uptime-prober:

  boot  -> alembic upgrade head, then backfill `jobtrends_backfill_months`
  loop  -> every `jobtrends_ingest_interval_seconds`, re-ingest the trailing
           `jobtrends_recent_months` (idempotent; catches each new monthly thread
           within a day of it being posted)

Start it with:  python -m app.jobtrends.worker
"""

from __future__ import annotations

import logging
import time

from app.config import settings
from app.db import SessionLocal
from app.jobtrends import migrate
from app.jobtrends.extract import rebuild_derived
from app.jobtrends.hn_algolia import HNAlgoliaClient
from app.jobtrends.ingest import ingest_recent

logger = logging.getLogger(__name__)


def _run_once(months: int) -> None:
    # Order matters: refresh ALL raw sources first (HN posts + every
    # continuous-board snapshot), THEN rebuild the derived tables — the derived
    # skill-demand rollup reads ats_jobs, so rebuilding before the snapshots would
    # leave it a tick stale (and mis-weight the cross-source shares). Each raw step
    # is isolated so one source's outage can't sink the others.
    client = HNAlgoliaClient()
    try:
        with SessionLocal() as session:
            result = ingest_recent(session, client, months)
        logger.info(
            "jobtrends: HN ingest — %s posts across %s stream-months",
            sum(result.values()),
            len(result),
        )
    except Exception:  # noqa: BLE001 — a bad source must not kill the loop
        logger.exception("jobtrends: HN ingest failed; will retry next interval")

    try:
        from app.jobtrends.ats import SEED_COMPANIES, AtsClient, ats_snapshot

        with SessionLocal() as session:
            ats_snapshot(session, AtsClient(), SEED_COMPANIES)
    except Exception:  # noqa: BLE001
        logger.exception("jobtrends: ATS snapshot failed; will retry next interval")

    try:
        from app.jobtrends.remote_boards import RemoteClient, remote_snapshot

        with SessionLocal() as session:
            remote_snapshot(session, RemoteClient())
    except Exception:  # noqa: BLE001
        logger.exception("jobtrends: remote snapshot failed; will retry next interval")

    # USAJobs — no-ops if no API key is configured.
    try:
        from app.jobtrends.usajobs import UsaJobsClient, usajobs_snapshot

        with SessionLocal() as session:
            usajobs_snapshot(session, UsaJobsClient())
    except Exception:  # noqa: BLE001
        logger.exception("jobtrends: USAJobs snapshot failed; will retry next interval")

    # WARN filings (supply side) — idempotent upsert of each state's feed.
    try:
        from app.jobtrends.warn import WarnClient, warn_ingest

        with SessionLocal() as session:
            warn_ingest(session, WarnClient())
    except Exception:  # noqa: BLE001
        logger.exception("jobtrends: WARN ingest failed; will retry next interval")

    # OEWS location wage bands — annual data, so refresh only if missing or stale.
    try:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import func, select

        from app.jobtrends.models import OewsWage
        from app.jobtrends.oews import STATES, OewsClient, load_oews

        with SessionLocal() as session:
            # Refresh if incomplete (a partial load left states missing) OR stale.
            # Gate on the count + OLDEST row, not the newest — a partial run commits
            # per state, so max(updated_at) could look fresh while most are absent.
            loaded = session.scalar(
                select(func.count())
                .select_from(OewsWage)
                .where(OewsWage.area_type == "state")
            )
            oldest = session.scalar(select(func.min(OewsWage.updated_at)))
            stale = oldest is None or oldest < datetime.now(UTC) - timedelta(days=30)
            if int(loaded or 0) < len(STATES) or stale:
                load_oews(session, OewsClient())
    except Exception:  # noqa: BLE001
        logger.exception("jobtrends: OEWS load failed; will retry next interval")

    # Commute-shed radar: sync the curated employer registry, then snapshot the
    # live-feed subset (source='commute_shed', filtered to in-reach roles).
    try:
        from app.jobtrends.ats import AtsClient
        from app.jobtrends.commute_shed import commute_shed_snapshot, sync_registry

        with SessionLocal() as session:
            sync_registry(session)
            commute_shed_snapshot(session, AtsClient())
    except Exception:  # noqa: BLE001
        logger.exception(
            "jobtrends: commute-shed snapshot failed; will retry next interval"
        )

    # Now that every raw source is fresh, rebuild all derived tables.
    try:
        with SessionLocal() as session:
            rebuild_derived(session)
        logger.info("jobtrends: tick complete — derived tables rebuilt")
    except Exception:  # noqa: BLE001
        logger.exception("jobtrends: derived rebuild failed; will retry next interval")


def _setup_logging() -> None:
    # force=True so a second call replaces handlers — alembic's fileConfig (run during
    # migrate) reconfigures the root logger to WARN, which would otherwise mute every
    # ingest INFO line and, worse, the error path from the log-shipper.
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", force=True
    )


def main() -> int:
    _setup_logging()
    logger.info("jobtrends worker starting")

    migrate.upgrade_to_head()
    _setup_logging()  # restore our logging after alembic's fileConfig

    # First pass: full backfill window.
    _run_once(settings.jobtrends_backfill_months)

    interval = settings.jobtrends_ingest_interval_seconds
    while True:
        time.sleep(interval)
        _run_once(settings.jobtrends_recent_months)


if __name__ == "__main__":
    raise SystemExit(main())
