import argparse
import json
import logging
from datetime import date
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.jobtrends.sec.client import SecClient
from app.jobtrends.sec.registry import load_registry, unmatched_employers
from app.jobtrends.sec.service import ingest, rebuild, report
from app.jobtrends.sec.text import load_rules


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="sec")
    parser.add_argument("--registry", default=settings.sec_registry_path)
    parser.add_argument("--rules", default=settings.sec_rules_path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("registry")
    ingest_parser = sub.add_parser("ingest")
    ingest_parser.add_argument("--cik", action="append", default=[])
    ingest_parser.add_argument("--periods", type=int, choices=range(1, 6), default=2)
    ingest_parser.add_argument("--max-filings", type=int, default=40)
    rebuild_parser = sub.add_parser("rebuild")
    rebuild_parser.add_argument("--cik")
    rebuild_parser.add_argument("--limit", type=int, default=200)
    report_parser = sub.add_parser("signals")
    report_parser.add_argument("--cik")
    report_parser.add_argument("--category")
    report_parser.add_argument("--since", type=date.fromisoformat)
    report_parser.add_argument("--until", type=date.fromisoformat)
    report_parser.add_argument("--form", choices=["10-K", "10-K/A"])
    report_parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    if args.command == "registry":
        issuers = load_registry(args.registry)
        print(
            json.dumps(
                {
                    "issuers": [issuer.model_dump() for issuer in issuers],
                    "unmatched_employers": unmatched_employers(issuers),
                },
                indent=2,
            )
        )
        return 0
    try:
        with SessionLocal() as session:
            if args.command == "ingest":
                issuers = load_registry(args.registry)
                if args.cik:
                    selected = {cik.zfill(10) for cik in args.cik}
                    if selected - {issuer.cik for issuer in issuers}:
                        parser.error("--cik must identify a configured registry issuer")
                    issuers = [issuer for issuer in issuers if issuer.cik in selected]
                with SecClient(
                    settings.sec_contact_email, Path(settings.sec_cache_dir)
                ) as client:
                    result = ingest(
                        session,
                        client,
                        issuers,
                        load_rules(args.rules),
                        args.periods,
                        args.max_filings,
                    )
                    result["http_requests"] = client.requests
                    result["submissions_cache_hits"] = client.cache_hits
            elif args.command == "rebuild":
                result = rebuild(session, load_rules(args.rules), args.cik, args.limit)
            else:
                result = report(
                    session,
                    args.cik,
                    args.category,
                    args.since,
                    args.until,
                    args.limit,
                    args.form,
                )
        print(json.dumps(result, indent=2))
        return 1 if result.get("failed", 0) else 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
