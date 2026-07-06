"""jobtrends CLI — ingestion + analysis + migration helpers.

    python -m app.jobtrends.cli ingest --months 18   # backfill N recent months (raw)
    python -m app.jobtrends.cli extract              # rebuild keyword stats from raw
    python -m app.jobtrends.cli trend python rust mcp # print share-of-postings trend
    python -m app.jobtrends.cli migrate              # alembic upgrade head

Ingest is idempotent and extract/trend read only stored data, so re-running is
always safe. Change taxonomy.py and re-run `extract` + `trend` without re-fetching.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import settings
from app.db import SessionLocal
from app.jobtrends import migrate
from app.jobtrends.extract import extract_all
from app.jobtrends.hn_algolia import HNAlgoliaClient
from app.jobtrends.ingest import ingest_recent
from app.jobtrends.trend import format_trend_table, keyword_trend


def _setup_logging() -> None:
    # force=True: re-assert INFO after alembic's fileConfig (run during migrate) resets
    # the root logger to WARN, which would otherwise mute the ingest logs.
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", force=True
    )


def _cmd_ingest(months: int, run_migrations: bool) -> int:
    if run_migrations:
        migrate.upgrade_to_head()
        _setup_logging()  # restore our logging after alembic's fileConfig
    client = HNAlgoliaClient()
    with SessionLocal() as session:
        result = ingest_recent(session, client, months)
    total = sum(result.values())
    for month in sorted(result):
        print(f"  {month}: {result[month]} posts")  # noqa: T201
    print(f"ingested {total} posts across {len(result)} months")  # noqa: T201
    return 0


def _cmd_extract() -> int:
    with SessionLocal() as session:
        summary = extract_all(session)
    print(  # noqa: T201
        f"extracted {summary['keywords']} keywords across {summary['months']} months "
        f"({summary['rows']} stat rows)"
    )
    return 0


def _cmd_trend(keywords: list[str]) -> int:
    with SessionLocal() as session:
        report = keyword_trend(session, keywords or None)
    print(format_trend_table(report))  # noqa: T201
    return 0


def _cmd_migrate() -> int:
    migrate.upgrade_to_head()
    print("migrations applied (alembic upgrade head)")  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
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

    sub.add_parser("extract", help="rebuild keyword stats from the raw corpus")

    p_trend = sub.add_parser("trend", help="print share-of-postings trend")
    p_trend.add_argument("keywords", nargs="*", help="keywords to show (default: all)")

    sub.add_parser("migrate", help="apply DB migrations (alembic upgrade head)")

    args = parser.parse_args(argv)
    if args.cmd == "ingest":
        return _cmd_ingest(args.months, run_migrations=not args.no_migrate)
    if args.cmd == "extract":
        return _cmd_extract()
    if args.cmd == "trend":
        return _cmd_trend(args.keywords)
    if args.cmd == "migrate":
        return _cmd_migrate()
    return 2


if __name__ == "__main__":
    sys.exit(main())
