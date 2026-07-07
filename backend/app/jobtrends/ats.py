"""ATS company-boards source: currently-open roles from public job-board APIs.

Unlike the HN monthly archive, an ATS board (Greenhouse today) exposes only the
roles open *right now*. So we snapshot on each run: upsert every current posting
(refreshing `last_seen` + mutable fields), then mark anything a company no longer
lists as closed. Open→closed history therefore accrues from first deploy forward.

`parse_greenhouse` is pure (JSON -> ParsedJob[]) and unit-testable; the client is
a thin httpx wrapper with an injectable transport for tests. Providers are pluggable
— Lever/Ashby slot in as new fetchers + PROVIDERS entries.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.jobtrends.models import AtsJob

logger = logging.getLogger(__name__)

PROVIDER_GREENHOUSE = "greenhouse"

# `source` groups continuous-board rows in the shared ats_jobs table: 'ats' =
# per-company ATS boards (Greenhouse); 'remote_board' = remote aggregators
# (Remotive/RemoteOK). Reports filter on it so each signal stays clean.
SOURCE_ATS = "ats"
SOURCE_REMOTE = "remote_board"


@dataclass(frozen=True)
class Company:
    name: str
    provider: str
    token: str


# Curated seed list — well-known tech companies on public Greenhouse boards
# (all verified live). Add rows freely; the snapshot picks them up next run.
SEED_COMPANIES: list[Company] = [
    Company(n, PROVIDER_GREENHOUSE, t)
    for n, t in [
        ("Stripe", "stripe"),
        ("Databricks", "databricks"),
        ("Anthropic", "anthropic"),
        ("Airbnb", "airbnb"),
        ("Cloudflare", "cloudflare"),
        ("Brex", "brex"),
        ("Affirm", "affirm"),
        ("Pinterest", "pinterest"),
        ("Reddit", "reddit"),
        ("Figma", "figma"),
        ("GitLab", "gitlab"),
        ("Twilio", "twilio"),
        ("Lyft", "lyft"),
        ("Instacart", "instacart"),
        ("Asana", "asana"),
        ("Coinbase", "coinbase"),
        ("Robinhood", "robinhood"),
        ("Discord", "discord"),
        ("Gusto", "gusto"),
        ("Vercel", "vercel"),
        ("Dropbox", "dropbox"),
    ]
]


@dataclass(frozen=True)
class ParsedJob:
    provider: str
    company_token: str
    company_name: str
    external_id: str
    title: str
    location: str | None
    department: str | None
    url: str | None
    content_text: str
    posted_at: datetime | None
    source: str = SOURCE_ATS

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.company_token}:{self.external_id}"


def _strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw))
    return re.sub(r"\s+", " ", text).strip()


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_greenhouse(company: Company, payload: dict[str, Any]) -> list[ParsedJob]:
    """Greenhouse `/boards/{token}/jobs?content=true` JSON -> ParsedJob[]. Pure."""
    jobs: list[ParsedJob] = []
    for j in payload.get("jobs") or []:
        depts = [d.get("name") for d in (j.get("departments") or []) if d.get("name")]
        loc = (j.get("location") or {}).get("name")
        jobs.append(
            ParsedJob(
                provider=PROVIDER_GREENHOUSE,
                company_token=company.token,
                company_name=company.name,
                external_id=str(j.get("id")),
                title=str(j.get("title") or "").strip(),
                location=loc or None,
                department=depts[0] if depts else None,
                url=j.get("absolute_url"),
                content_text=_strip_html(j.get("content")),
                posted_at=_parse_dt(j.get("updated_at") or j.get("first_published")),
            )
        )
    return jobs


class AtsClient:
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

    def fetch_company(self, company: Company) -> list[ParsedJob]:
        if company.provider != PROVIDER_GREENHOUSE:
            raise ValueError(f"unsupported ATS provider: {company.provider}")
        url = f"https://boards-api.greenhouse.io/v1/boards/{company.token}/jobs"
        with httpx.Client(
            timeout=self.timeout_seconds, transport=self._transport
        ) as client:
            resp = client.get(
                url,
                params={"content": "true"},
                headers={"User-Agent": self._user_agent},
            )
            resp.raise_for_status()
            return parse_greenhouse(company, resp.json())


def upsert_jobs(session: Session, jobs: list[ParsedJob], run_at: datetime) -> None:
    """Upsert postings for this snapshot run. Shared by ATS + remote boards."""
    if not jobs:
        return
    rows = [
        {
            "id": j.id,
            "source": j.source,
            "provider": j.provider,
            "company_token": j.company_token,
            "company_name": j.company_name,
            "external_id": j.external_id,
            "title": j.title,
            "location": j.location,
            "department": j.department,
            "url": j.url,
            "content_text": j.content_text,
            "posted_at": j.posted_at,
            "first_seen": run_at,
            "last_seen": run_at,
            "is_open": True,
        }
        for j in jobs
    ]
    stmt = pg_insert(AtsJob).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AtsJob.id],
        set_={
            # first_seen + source intentionally NOT updated — the role's debut/origin.
            "company_name": stmt.excluded.company_name,
            "title": stmt.excluded.title,
            "location": stmt.excluded.location,
            "department": stmt.excluded.department,
            "url": stmt.excluded.url,
            "content_text": stmt.excluded.content_text,
            "posted_at": stmt.excluded.posted_at,
            "last_seen": stmt.excluded.last_seen,
            "is_open": True,
        },
    )
    session.execute(stmt)


def close_missing(
    session: Session,
    run_at: datetime,
    *,
    provider: str,
    company_token: str | None = None,
) -> None:
    """Flip roles not seen in this run to closed, within a provider (optionally a
    single company). Everything fetched this run has last_seen == run_at."""
    conditions = [
        AtsJob.provider == provider,
        AtsJob.last_seen < run_at,
        AtsJob.is_open.is_(True),
    ]
    if company_token is not None:
        conditions.append(AtsJob.company_token == company_token)
    session.execute(update(AtsJob).where(*conditions).values(is_open=False))


def ats_snapshot(
    session: Session, client: AtsClient, companies: list[Company]
) -> dict[str, int]:
    """Snapshot every company's open roles. Returns {companies_ok, open_roles}.

    Per company: upsert current postings with this run's timestamp, then flip any
    role we didn't see this run to closed. One bad company is logged and skipped.
    """
    run_at = datetime.now(tz=UTC)
    ok = 0
    for company in companies:
        try:
            jobs = client.fetch_company(company)
        except Exception:  # noqa: BLE001 — one board must not sink the whole snapshot
            logger.exception("jobtrends: ATS fetch failed for %s", company.token)
            continue
        upsert_jobs(session, jobs, run_at)
        # Roles this company no longer lists → closed.
        close_missing(
            session, run_at, provider=company.provider, company_token=company.token
        )
        session.commit()
        ok += 1
        logger.info("jobtrends: ATS %s — %s open roles", company.token, len(jobs))

    open_roles = session.scalar(
        select(func.count())
        .select_from(AtsJob)
        .where(AtsJob.is_open.is_(True), AtsJob.source == SOURCE_ATS)
    )
    logger.info(
        "jobtrends: ATS snapshot complete — %s companies, %s open roles", ok, open_roles
    )
    return {"companies_ok": ok, "open_roles": int(open_roles or 0)}


@dataclass(frozen=True)
class CompanyOpenings:
    company_name: str
    company_token: str
    open_roles: int


@dataclass(frozen=True)
class AtsReport:
    total_open: int
    companies: int
    top: list[CompanyOpenings]


def ats_report(session: Session, limit: int = 20) -> AtsReport:
    """Current open-role counts per company, ranked."""
    rows = session.execute(
        select(
            AtsJob.company_name,
            AtsJob.company_token,
            func.count(),
        )
        .where(AtsJob.is_open.is_(True), AtsJob.source == SOURCE_ATS)
        .group_by(AtsJob.company_name, AtsJob.company_token)
        .order_by(func.count().desc())
    ).all()
    companies = [
        CompanyOpenings(company_name=name, company_token=token, open_roles=n)
        for name, token, n in rows
    ]
    return AtsReport(
        total_open=sum(c.open_roles for c in companies),
        companies=len(companies),
        top=companies[:limit],
    )


def format_ats_table(report: AtsReport) -> str:
    if not report.companies:
        return "no data — run `ats-snapshot` first"
    lines = [
        f"{report.total_open} open roles across {report.companies} companies",
        "",
        f"{'company':<20}{'open roles':>12}",
        "-" * 32,
    ]
    for c in report.top:
        lines.append(f"{c.company_name:<20}{c.open_roles:>12}")
    return "\n".join(lines)
