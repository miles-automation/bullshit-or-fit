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
from app.jobtrends.hn_algolia import HNAlgoliaClient
from app.jobtrends.ingest import ingest_recent

logger = logging.getLogger(__name__)


def _run_once(months: int) -> None:
    client = HNAlgoliaClient()
    try:
        with SessionLocal() as session:
            result = ingest_recent(session, client, months)
        logger.info(
            "jobtrends: tick complete — %s posts across %s months",
            sum(result.values()),
            len(result),
        )
    except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
        logger.exception("jobtrends: ingest tick failed; will retry next interval")


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
