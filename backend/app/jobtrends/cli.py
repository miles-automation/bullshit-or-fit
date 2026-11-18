"""jobtrends CLI — ingestion + analysis + migration helpers.

    python -m app.jobtrends.cli ingest --months 18   # backfill N recent months (raw)
    python -m app.jobtrends.cli extract              # rebuild ALL derived tables
    python -m app.jobtrends.cli trend python rust mcp # keyword share-of-postings
    python -m app.jobtrends.cli comp                 # salary coverage + quartiles
    python -m app.jobtrends.cli churn                # author recurrence + churn
    python -m app.jobtrends.cli market               # demand/supply per month
    python -m app.jobtrends.cli migrate              # alembic upgrade head

Ingest is idempotent and the reports read only stored data, so re-running is
always safe. Change taxonomy.py/comp.py and re-run `extract` without re-fetching.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.jobtrends import migrate
from app.jobtrends.ats import (
    SEED_COMPANIES,
    AtsClient,
    ats_report,
    ats_snapshot,
    format_ats_table,
)
from app.jobtrends.comp import comp_trend, format_comp_table
from app.jobtrends.extract import rebuild_derived
from app.jobtrends.hn_algolia import HNAlgoliaClient
from app.jobtrends.ingest import ingest_recent
from app.jobtrends.market import format_market_table, market_report
from app.jobtrends.recurrence import churn_report, format_churn_table
from app.jobtrends.skill_demand import format_skill_table, skill_demand
from app.jobtrends.remote_boards import (
    RemoteClient,
    format_remote_table,
    remote_report,
    remote_snapshot,
)
from app.jobtrends.trend import format_trend_table, keyword_trend
from app.jobtrends.usajobs import (
    UsaJobsClient,
    format_usajobs_table,
    usajobs_report,
    usajobs_snapshot,
)


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
        rebuild_derived(session)
    print("rebuilt derived tables (keyword stats, comp, cohorts)")  # noqa: T201
    return 0


def _cmd_trend(keywords: list[str]) -> int:
    with SessionLocal() as session:
        report = keyword_trend(session, keywords or None)
    print(format_trend_table(report))  # noqa: T201
    return 0


def _cmd_comp() -> int:
    with SessionLocal() as session:
        print(format_comp_table(comp_trend(session)))  # noqa: T201
    return 0


def _cmd_churn() -> int:
    with SessionLocal() as session:
        print(format_churn_table(churn_report(session)))  # noqa: T201
    return 0


def _cmd_market() -> int:
    with SessionLocal() as session:
        print(format_market_table(market_report(session)))  # noqa: T201
    return 0


def _cmd_ats_snapshot() -> int:
    with SessionLocal() as session:
        summary = ats_snapshot(session, AtsClient(), SEED_COMPANIES)
    print(  # noqa: T201
        f"snapshot: {summary['open_roles']} open roles across "
        f"{summary['companies_ok']} companies"
    )
    return 0


def _cmd_ats() -> int:
    with SessionLocal() as session:
        print(format_ats_table(ats_report(session)))  # noqa: T201
    return 0


def _cmd_remote_snapshot() -> int:
    with SessionLocal() as session:
        summary = remote_snapshot(session, RemoteClient())
    print(  # noqa: T201
        f"snapshot: {summary['open_roles']} open remote roles from "
        f"{summary['providers_ok']} boards"
    )
    return 0


def _cmd_remote() -> int:
    with SessionLocal() as session:
        print(format_remote_table(remote_report(session)))  # noqa: T201
    return 0


def _cmd_usajobs_snapshot() -> int:
    with SessionLocal() as session:
        summary = usajobs_snapshot(session, UsaJobsClient())
    if not summary["configured"]:
        print("skipped: set USAJOBS_API_KEY + USAJOBS_USER_AGENT (email)")  # noqa: T201
        return 0
    print(f"snapshot: {summary['open_roles']} federal roles tracked")  # noqa: T201
    return 0


def _cmd_usajobs() -> int:
    with SessionLocal() as session:
        print(format_usajobs_table(usajobs_report(session)))  # noqa: T201
    return 0


def _cmd_skills() -> int:
    with SessionLocal() as session:
        print(format_skill_table(skill_demand(session)))  # noqa: T201
    return 0


def _cmd_warn_ingest() -> int:
    from app.jobtrends.warn import WarnClient, warn_ingest

    with SessionLocal() as session:
        result = warn_ingest(session, WarnClient())
    print(f"warn: ingested {sum(result.values())} notices {result}")  # noqa: T201
    return 0


def _cmd_warn() -> int:
    from app.jobtrends.warn import format_warn_table, warn_report

    with SessionLocal() as session:
        print(format_warn_table(warn_report(session)))  # noqa: T201
    return 0


def _cmd_oews_load() -> int:
    from app.jobtrends.oews import OewsClient, load_oews

    with SessionLocal() as session:
        result = load_oews(session, OewsClient())
    print(f"oews: loaded {result['states']} states")  # noqa: T201
    return 0


def _cmd_wages(area: str) -> int:
    from app.jobtrends.oews import wage_bands

    with SessionLocal() as session:
        r = wage_bands(session, area)
    for label, b in (("national", r.national), (area.upper(), r.area)):
        if b:
            print(  # noqa: T201
                f"  {label:<14} p10 ${b.p10_usd // 1000}k | median ${b.median_usd // 1000}k"
                f" | p90 ${b.p90_usd // 1000}k"
            )
    return 0


def _cmd_migrate() -> int:
    migrate.upgrade_to_head()
    print("migrations applied (alembic upgrade head)")  # noqa: T201
    return 0


def _cmd_discover() -> int:
    from app.jobtrends.ats import AtsClient
    from app.jobtrends.discovery import BoardProber, discovery_tick

    prober = BoardProber()
    try:
        with SessionLocal() as session:
            summary = discovery_tick(session, prober, AtsClient())
    finally:
        prober.close()
    print(  # noqa: T201
        f"discovery: {summary['candidates']} candidates, probed {summary['probed']}, "
        f"found {summary['found']}, {summary['boards']} boards snapshotted"
    )
    return 0


def _cmd_himalayas_snapshot() -> int:
    from app.jobtrends.himalayas import HimalayasClient, himalayas_snapshot

    with SessionLocal() as session:
        summary = himalayas_snapshot(session, HimalayasClient())
    print(f"himalayas: {summary['fetched']} fetched, {summary['new']} new")  # noqa: T201
    return 0


def _cmd_fit(days: int, max_age: int, output: str) -> int:
    from app.jobtrends.fit import fit_roles, format_fit_list
    from app.jobtrends.fit_export import export_fit

    with SessionLocal() as session:
        if output == "json":
            document = export_fit(session, days=days, max_age_days=max_age)
            print(json.dumps(document, indent=2))  # noqa: T201
            return 0
        result = fit_roles(session, days=days, max_age_days=max_age)
        print(format_fit_list(result.roles, days, hidden=result.hidden))  # noqa: T201
    return 0


def _cmd_fit_decisions_sync(path: str, allow_removals: bool) -> int:
    from app.jobtrends.decisions import TrackerError, parse_tracker, sync_decisions

    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        rows = parse_tracker(text)
        if not rows:
            raise TrackerError("no tracker rows found")
        with SessionLocal() as session:
            summary = sync_decisions(session, rows, allow_removals=allow_removals)
    except TrackerError as exc:
        print(f"fit-decisions-sync: {exc}; nothing changed", file=sys.stderr)  # noqa: T201
        return 1
    print(  # noqa: T201
        f"fit decisions: {summary.total} synced, {summary.removed} removed"
    )
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

    sub.add_parser("extract", help="rebuild all derived tables from the raw corpus")

    p_trend = sub.add_parser("trend", help="keyword share-of-postings trend")
    p_trend.add_argument("keywords", nargs="*", help="keywords to show (default: all)")

    sub.add_parser("comp", help="salary coverage + midpoint quartiles by month")
    sub.add_parser("churn", help="author recurrence + monthly churn")
    sub.add_parser("market", help="demand/supply — job-seekers per opening")
    sub.add_parser("ats-snapshot", help="snapshot ATS company boards (Greenhouse)")
    sub.add_parser("ats", help="open roles per company (from the latest snapshot)")
    sub.add_parser("remote-snapshot", help="snapshot remote boards (Remotive/RemoteOK)")
    sub.add_parser("remote", help="remote job market — open roles by category")
    sub.add_parser("usajobs-snapshot", help="snapshot USAJobs (federal; needs API key)")
    sub.add_parser("usajobs", help="federal roles by agency/category")
    sub.add_parser("skills", help="cross-source skill demand across live openings")
    sub.add_parser("warn-ingest", help="ingest WARN Act layoff filings (TX/OR)")
    sub.add_parser("warn", help="WARN layoff filings — recent notices + totals")
    sub.add_parser("oews-load", help="load BLS OEWS wage bands by state (via O*NET)")
    p_wages = sub.add_parser("wages", help="OEWS wage bands for a state (+ national)")
    p_wages.add_argument("area", help="2-letter state code, e.g. WY")

    sub.add_parser("discover", help="find small-company boards (YC) and snapshot them")
    sub.add_parser("himalayas-snapshot", help="snapshot Himalayas remote US roles")
    p_fit = sub.add_parser("fit", help="new roles that fit Rich's search filters")
    p_fit.add_argument("--days", type=int, default=1)
    p_fit.add_argument("--max-age", type=int, default=30)
    p_fit.add_argument("--format", choices=["text", "json"], default="text")
    p_sync = sub.add_parser(
        "fit-decisions-sync",
        help="replace applied/skipped roles with the rows of a tracker table",
    )
    p_sync.add_argument("path", help="tracker markdown file, or - for stdin")
    p_sync.add_argument("--allow-removals", action="store_true")

    sub.add_parser("migrate", help="apply DB migrations (alembic upgrade head)")

    args = parser.parse_args(argv)
    if args.cmd == "ingest":
        return _cmd_ingest(args.months, run_migrations=not args.no_migrate)
    if args.cmd == "extract":
        return _cmd_extract()
    if args.cmd == "trend":
        return _cmd_trend(args.keywords)
    if args.cmd == "comp":
        return _cmd_comp()
    if args.cmd == "churn":
        return _cmd_churn()
    if args.cmd == "market":
        return _cmd_market()
    if args.cmd == "ats-snapshot":
        return _cmd_ats_snapshot()
    if args.cmd == "ats":
        return _cmd_ats()
    if args.cmd == "remote-snapshot":
        return _cmd_remote_snapshot()
    if args.cmd == "remote":
        return _cmd_remote()
    if args.cmd == "usajobs-snapshot":
        return _cmd_usajobs_snapshot()
    if args.cmd == "usajobs":
        return _cmd_usajobs()
    if args.cmd == "warn-ingest":
        return _cmd_warn_ingest()
    if args.cmd == "warn":
        return _cmd_warn()
    if args.cmd == "oews-load":
        return _cmd_oews_load()
    if args.cmd == "wages":
        return _cmd_wages(args.area)
    if args.cmd == "skills":
        return _cmd_skills()
    if args.cmd == "discover":
        return _cmd_discover()
    if args.cmd == "himalayas-snapshot":
        return _cmd_himalayas_snapshot()
    if args.cmd == "fit":
        return _cmd_fit(args.days, args.max_age, args.format)
    if args.cmd == "fit-decisions-sync":
        return _cmd_fit_decisions_sync(args.path, args.allow_removals)
    if args.cmd == "migrate":
        return _cmd_migrate()
    return 2


if __name__ == "__main__":
    sys.exit(main())
