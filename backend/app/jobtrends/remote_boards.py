"""Remote-job-board aggregators: Remotive + RemoteOK.

Continuous-board sources like ATS, so they reuse the shared `ats_jobs` raw table
(source='remote_board') and the snapshot upsert/close helpers. Unlike ATS's
per-company boards, each provider is one aggregated feed, so close-detection is
per *provider*, not per company. The free public feeds are capped (Remotive
returns a recent sample, RemoteOK the latest ~100), so coverage accrues over time
as the snapshot history builds.

`parse_remotive`/`parse_remoteok` are pure; RemoteClient is httpx with an
injectable transport for tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.jobtrends.ats import (
    SOURCE_REMOTE,
    ParsedJob,
    _parse_dt,
    _slug_hint,
    _strip_html,
    close_missing,
    upsert_jobs,
)
from app.jobtrends.models import AtsJob

logger = logging.getLogger(__name__)

PROVIDER_REMOTIVE = "remotive"
PROVIDER_REMOTEOK = "remoteok"
REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
REMOTEOK_URL = "https://remoteok.com/api"


def parse_remotive(payload: dict[str, Any]) -> list[ParsedJob]:
    jobs: list[ParsedJob] = []
    for j in payload.get("jobs") or []:
        company = str(j.get("company_name") or "").strip()
        jobs.append(
            ParsedJob(
                source=SOURCE_REMOTE,
                provider=PROVIDER_REMOTIVE,
                company_token=_slug_hint(company),
                company_name=company or "Unknown",
                external_id=str(j.get("id")),
                title=str(j.get("title") or "").strip(),
                location=(j.get("candidate_required_location") or None),
                department=(j.get("category") or None),  # category as the "function"
                url=j.get("url"),
                content_text=_strip_html(j.get("description")),
                posted_at=_parse_dt(j.get("publication_date")),
            )
        )
    return jobs


def parse_remoteok(payload: list[Any]) -> list[ParsedJob]:
    jobs: list[ParsedJob] = []
    for j in payload:
        # The first array element is a legal/metadata notice, not a job.
        if not isinstance(j, dict) or not j.get("position"):
            continue
        company = str(j.get("company") or "").strip()
        jobs.append(
            ParsedJob(
                source=SOURCE_REMOTE,
                provider=PROVIDER_REMOTEOK,
                company_token=_slug_hint(company),
                company_name=company or "Unknown",
                external_id=str(j.get("id")),
                title=str(j.get("position") or "").strip(),
                location=(j.get("location") or None),
                department=None,
                url=j.get("url"),
                content_text=_strip_html(j.get("description")),
                posted_at=_parse_dt(j.get("date")),
            )
        )
    return jobs


class RemoteClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._user_agent = user_agent or settings.jobtrends_user_agent

    def _get(self, url: str) -> Any:
        with httpx.Client(
            timeout=self.timeout_seconds, transport=self._transport
        ) as client:
            resp = client.get(url, headers={"User-Agent": self._user_agent})
            resp.raise_for_status()
            return resp.json()

    def fetch_remotive(self) -> list[ParsedJob]:
        return parse_remotive(self._get(REMOTIVE_URL))

    def fetch_remoteok(self) -> list[ParsedJob]:
        return parse_remoteok(self._get(REMOTEOK_URL))


def remote_snapshot(session: Session, client: RemoteClient) -> dict[str, int]:
    """Snapshot both remote boards. Returns {providers_ok, open_roles}."""
    run_at = datetime.now(tz=UTC)
    fetchers = [
        (PROVIDER_REMOTIVE, client.fetch_remotive),
        (PROVIDER_REMOTEOK, client.fetch_remoteok),
    ]
    ok = 0
    for provider, fetch in fetchers:
        try:
            jobs = fetch()
        except Exception:  # noqa: BLE001 — one board must not sink the snapshot
            logger.exception("jobtrends: remote fetch failed for %s", provider)
            continue
        upsert_jobs(session, jobs, run_at)
        close_missing(session, run_at, provider=provider)
        session.commit()
        ok += 1
        logger.info("jobtrends: remote %s — %s open roles", provider, len(jobs))

    open_roles = session.scalar(
        select(func.count())
        .select_from(AtsJob)
        .where(AtsJob.is_open.is_(True), AtsJob.source == SOURCE_REMOTE)
    )
    logger.info(
        "jobtrends: remote snapshot complete — %s providers, %s open roles",
        ok,
        open_roles,
    )
    return {"providers_ok": ok, "open_roles": int(open_roles or 0)}


@dataclass(frozen=True)
class NameCount:
    name: str
    open_roles: int


@dataclass(frozen=True)
class RemoteReport:
    total_open: int
    companies: int
    top_companies: list[NameCount]
    top_categories: list[NameCount]


def _top(session: Session, column: Any, limit: int) -> list[NameCount]:
    rows = session.execute(
        select(column, func.count())
        .where(
            AtsJob.is_open.is_(True), AtsJob.source == SOURCE_REMOTE, column.isnot(None)
        )
        .group_by(column)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [NameCount(name=name, open_roles=n) for name, n in rows]


def remote_report(session: Session, limit: int = 12) -> RemoteReport:
    total = session.scalar(
        select(func.count())
        .select_from(AtsJob)
        .where(AtsJob.is_open.is_(True), AtsJob.source == SOURCE_REMOTE)
    )
    companies = session.scalar(
        select(func.count(func.distinct(AtsJob.company_token))).where(
            AtsJob.is_open.is_(True), AtsJob.source == SOURCE_REMOTE
        )
    )
    return RemoteReport(
        total_open=int(total or 0),
        companies=int(companies or 0),
        top_companies=_top(session, AtsJob.company_name, limit),
        top_categories=_top(session, AtsJob.department, limit),
    )


def format_remote_table(report: RemoteReport) -> str:
    if not report.total_open:
        return "no data — run `remote-snapshot` first"
    lines = [
        f"{report.total_open} open remote roles across {report.companies} companies",
        "",
        "top categories:",
    ]
    for c in report.top_categories:
        lines.append(f"  {c.name:<28}{c.open_roles:>5}")
    return "\n".join(lines)
