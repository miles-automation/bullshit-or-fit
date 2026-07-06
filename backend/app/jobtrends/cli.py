"""jobtrends CLI — one-shot ingestion + migration helpers.

    python -m app.jobtrends.cli ingest --months 18   # backfill N recent months
    python -m app.jobtrends.cli migrate              # alembic upgrade head

Ingestion is idempotent, so re-running is always safe.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import settings
from app.db import SessionLocal
from app.jobtrends import migrate
from app.jobtrends.hn_algolia import HNAlgoliaClient
from app.jobtrends.ingest import ingest_recent


def _cmd_ingest(months: int, run_migrations: bool) -> int:
    if run_migrations:
        migrate.upgrade_to_head()
    client = HNAlgoliaClient()
    with SessionLocal() as session:
        result = ingest_recent(session, client, months)
    total = sum(result.values())
    for month in sorted(result):
        print(f"  {month}: {result[month]} posts")  # noqa: T201
    print(f"ingested {total} posts across {len(result)} months")  # noqa: T201
    return 0


def _cmd_migrate() -> int:
    migrate.upgrade_to_head()
    print("migrations applied (alembic upgrade head)")  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="jobtrends")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="backfill/refresh N recent months")
    p_ingest.add_argument(
        "--months", type=int, default=settings.jobtrends_backfill_months
    )
    p_ingest.add_argument(
        "--no-migrate",
        action="store_true",
        help="skip `alembic upgrade head` before ingesting",
    )

    sub.add_parser("migrate", help="apply DB migrations (alembic upgrade head)")

    args = parser.parse_args(argv)
    if args.cmd == "ingest":
        return _cmd_ingest(args.months, run_migrations=not args.no_migrate)
    if args.cmd == "migrate":
        return _cmd_migrate()
    return 2


if __name__ == "__main__":
    sys.exit(main())
