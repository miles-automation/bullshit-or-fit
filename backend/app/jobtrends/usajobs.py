"""USAJobs — the US government's official federal-jobs API.

Free, self-service API key (developer.usajobs.gov, emailed on signup). A
continuous-board source like ATS/remote, so it reuses the shared `ats_jobs` table
(source='usajobs') and snapshot helpers, with per-provider close-detection.

`parse_usajobs` is pure (JSON -> (jobs, total_available)); UsaJobsClient is httpx
with an injectable transport. If no API key is configured the snapshot is skipped
gracefully — the rest of the pipeline is unaffected.
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
    SOURCE_USAJOBS,
    ParsedJob,
    _parse_dt,
    _slug_hint,
    _strip_html,
    close_missing,
    upsert_jobs,
)
from app.jobtrends.models import AtsJob

logger = logging.getLogger(__name__)

PROVIDER_USAJOBS = "usajobs"
USAJOBS_URL = "https://data.usajobs.gov/api/search"


def _first(seq: Any, key: str) -> str | None:
    if isinstance(seq, list) and seq:
        val = seq[0].get(key)
        return str(val) if val not in (None, "") else None
    return None


def parse_usajobs(payload: dict[str, Any]) -> tuple[list[ParsedJob], int]:
    """USAJobs /api/search JSON -> (ParsedJob[], total_available). Pure."""
    result = payload.get("SearchResult") or {}
    total = int(result.get("SearchResultCountAll") or 0)
    jobs: list[ParsedJob] = []
    for item in result.get("SearchResultItems") or []:
        d = item.get("MatchedObjectDescriptor") or {}
        # Agency is the employer; job category is the "function".
        agency = str(d.get("OrganizationName") or d.get("DepartmentName") or "").strip()
        category = _first(d.get("JobCategory"), "Name")
        external_id = str(
            item.get("MatchedObjectId") or d.get("PositionID") or ""
        ).strip()
        if not external_id or not agency:
            continue
        summary = ((d.get("UserArea") or {}).get("Details") or {}).get("JobSummary")
        jobs.append(
            ParsedJob(
                source=SOURCE_USAJOBS,
                provider=PROVIDER_USAJOBS,
                company_token=_slug_hint(agency),
                company_name=agency,
                external_id=external_id,
                title=str(d.get("PositionTitle") or "").strip(),
                location=(d.get("PositionLocationDisplay") or None),
                department=category,
                url=d.get("PositionURI"),
                content_text=_strip_html(summary),
                posted_at=_parse_dt(d.get("PublicationStartDate")),
            )
        )
    return jobs, total


class UsaJobsClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        user_agent: str | None = None,
        proxy: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = settings.usajobs_api_key if api_key is None else api_key
        self.user_agent = user_agent or settings.usajobs_user_agent
        self.proxy = settings.usajobs_proxy if proxy is None else proxy
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.user_agent)

    def search(self, page: int, results_per_page: int = 500) -> dict[str, Any]:
        headers = {
            "Authorization-Key": self.api_key,
            "User-Agent": self.user_agent,
            "Host": "data.usajobs.gov",
        }
        params = {"Page": page, "ResultsPerPage": results_per_page}
        # A mock transport (tests) takes precedence; otherwise route via the proxy
        # when one is configured. httpx forbids passing both.
        proxy = self.proxy or None if self._transport is None else None
        with httpx.Client(
            timeout=self.timeout_seconds, transport=self._transport, proxy=proxy
        ) as client:
            resp = client.get(USAJOBS_URL, params=params, headers=headers)
            resp.raise_for_status()
            return resp.json()


def usajobs_snapshot(
    session: Session, client: UsaJobsClient, max_pages: int | None = None
) -> dict[str, int]:
    """Snapshot a bounded sample of recent federal postings. {configured, open_roles}."""
    if not client.configured:
        logger.info("jobtrends: USAJobs snapshot skipped (no API key configured)")
        return {"configured": 0, "open_roles": 0}

    pages = max_pages if max_pages is not None else settings.usajobs_max_pages
    run_at = datetime.now(tz=UTC)
    fetched = 0
    for page in range(1, pages + 1):
        try:
            payload = client.search(page)
        except httpx.HTTPError as exc:
            # USAJobs is behind Akamai, which IP-blocks many datacenter/cloud egress
            # IPs (an edge 403). Degrade to a warning and leave existing rows intact —
            # do NOT close_missing on a failed fetch, or a transient block would wrongly
            # flip every federal role to closed.
            logger.warning(
                "jobtrends: USAJobs fetch failed on page %s (%s) — likely a CDN/IP "
                "block from this host; leaving prior data intact",
                page,
                exc.__class__.__name__,
            )
            return {"configured": 1, "blocked": 1, "open_roles": _open_count(session)}
        jobs, total = parse_usajobs(payload)
        if not jobs:
            break
        upsert_jobs(session, jobs, run_at)
        session.commit()
        fetched += len(jobs)
        # SearchResultCountAll is capped at 10k by USAJobs; stop once we've pulled it.
        if fetched >= total:
            break
    close_missing(session, run_at, provider=PROVIDER_USAJOBS)
    session.commit()

    open_roles = _open_count(session)
    logger.info(
        "jobtrends: USAJobs snapshot complete — %s fetched, %s open",
        fetched,
        open_roles,
    )
    return {"configured": 1, "blocked": 0, "open_roles": open_roles}


def _open_count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(AtsJob)
            .where(AtsJob.is_open.is_(True), AtsJob.source == SOURCE_USAJOBS)
        )
        or 0
    )


@dataclass(frozen=True)
class NameCount:
    name: str
    open_roles: int


@dataclass(frozen=True)
class UsaJobsReport:
    total_open: int
    agencies: int
    top_agencies: list[NameCount]
    top_categories: list[NameCount]


def _top(session: Session, column: Any, limit: int) -> list[NameCount]:
    rows = session.execute(
        select(column, func.count())
        .where(
            AtsJob.is_open.is_(True),
            AtsJob.source == SOURCE_USAJOBS,
            column.isnot(None),
        )
        .group_by(column)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [NameCount(name=name, open_roles=n) for name, n in rows]


def usajobs_report(session: Session, limit: int = 12) -> UsaJobsReport:
    total = session.scalar(
        select(func.count())
        .select_from(AtsJob)
        .where(AtsJob.is_open.is_(True), AtsJob.source == SOURCE_USAJOBS)
    )
    agencies = session.scalar(
        select(func.count(func.distinct(AtsJob.company_token))).where(
            AtsJob.is_open.is_(True), AtsJob.source == SOURCE_USAJOBS
        )
    )
    return UsaJobsReport(
        total_open=int(total or 0),
        agencies=int(agencies or 0),
        top_agencies=_top(session, AtsJob.company_name, limit),
        top_categories=_top(session, AtsJob.department, limit),
    )


def format_usajobs_table(report: UsaJobsReport) -> str:
    if not report.total_open:
        return "no data — run `usajobs-snapshot` (needs an API key)"
    lines = [
        f"{report.total_open} federal roles tracked across {report.agencies} agencies",
        "",
        "top agencies:",
    ]
    for a in report.top_agencies:
        lines.append(f"  {a.name[:36]:<38}{a.open_roles:>5}")
    return "\n".join(lines)
