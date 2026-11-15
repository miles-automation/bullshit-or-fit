from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.jobtrends.ats import ParsedJob, _strip_html, upsert_jobs
from app.jobtrends.models import AtsJob

logger = logging.getLogger(__name__)

SOURCE_HIMALAYAS = "himalayas"
PROVIDER_HIMALAYAS = "himalayas"
SEARCH_URL = "https://himalayas.app/jobs/api/search"
QUERIES = ("software engineer", "developer")
MAX_PAGES_PER_QUERY = 15
UNSEEN_CLOSE_AFTER = timedelta(days=30)

_PERIOD_MULT: dict[str, int] = {
    "annual": 1,
    "yearly": 1,
    "year": 1,
    "monthly": 12,
    "month": 12,
    "weekly": 52,
    "week": 52,
    "daily": 260,
    "day": 260,
    "hourly": 2080,
    "hour": 2080,
}


def _epoch_dt(value: Any) -> datetime | None:
    try:
        seconds = float(str(value))
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _comp(job: dict[str, Any]) -> dict[str, Any] | None:
    from app.jobtrends.comp import annualize_structured

    if (job.get("currency") or "USD") != "USD":
        return None
    mult = _PERIOD_MULT.get(str(job.get("salaryPeriod") or "annual").strip().lower())
    if mult is None:
        return None
    low_raw = job.get("minSalary")
    high_raw = job.get("maxSalary")
    if low_raw in (None, "") and high_raw in (None, ""):
        return None
    try:
        lo = float(str(low_raw if low_raw not in (None, "") else high_raw)) * mult
        hi = float(str(high_raw if high_raw not in (None, "") else low_raw)) * mult
    except (TypeError, ValueError):
        return None
    annual = annualize_structured(lo, hi, "per year")
    if annual is None:
        return None
    return {
        "comp_min": annual[0],
        "comp_max": annual[1],
        "comp_currency": "USD",
        "comp_period": "year",
        "comp_kind": "structured",
    }


def _external_id(job: dict[str, Any]) -> str | None:
    guid = str(job.get("guid") or "")
    marker = "/companies/"
    if marker in guid:
        return guid.split(marker, 1)[1].split("?", 1)[0].strip("/") or None
    return None


def _location(job: dict[str, Any]) -> str:
    places = [str(p) for p in job.get("locationRestrictions") or [] if p]
    if not places:
        return "Remote (Worldwide)"
    if "United States" in places and len(places) > 3:
        return f"Remote (United States + {len(places) - 1} more)"
    return f"Remote ({', '.join(places)})"


def parse_himalayas(payload: Any) -> list[ParsedJob]:
    jobs: list[ParsedJob] = []
    for j in (payload or {}).get("jobs") or []:
        if not isinstance(j, dict):
            continue
        external_id = _external_id(j)
        title = str(j.get("title") or "").strip()
        if not external_id or not title:
            continue
        company = str(j.get("companyName") or "").strip() or "Unknown"
        parents = [str(p) for p in j.get("parentCategories") or [] if p]
        jobs.append(
            ParsedJob(
                source=SOURCE_HIMALAYAS,
                provider=PROVIDER_HIMALAYAS,
                company_token=str(j.get("companySlug") or external_id.split("/", 1)[0]),
                company_name=company,
                external_id=external_id,
                title=title,
                location=_location(j),
                department=parents[0] if parents else None,
                url=j.get("applicationLink") or j.get("guid"),
                content_text=_strip_html(j.get("description") or j.get("excerpt")),
                posted_at=_epoch_dt(j.get("pubDate")),
                expires_at=_epoch_dt(j.get("expiryDate")),
                **(_comp(j) or {}),
            )
        )
    return jobs


class RateLimitedError(Exception):
    pass


class HimalayasClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        pause_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._timeout = timeout_seconds
        self._transport = transport
        self._pause = pause_seconds
        self._sleep = sleep

    def search(self, query: str, page: int) -> list[ParsedJob]:
        self._sleep(self._pause)
        params: dict[str, str | int] = {
            "q": query,
            "country": "US",
            "seniority": "Senior",
            "employment_type": "Full Time",
            "sort": "recent",
            "page": page,
        }
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            resp = client.get(
                SEARCH_URL,
                params=params,
                headers={"User-Agent": settings.jobtrends_user_agent},
            )
            if resp.status_code == 429:
                raise RateLimitedError(resp.headers.get("Retry-After"))
            resp.raise_for_status()
            return parse_himalayas(resp.json())


def _known_ids(session: Session, ids: list[str], before: datetime) -> set[str]:
    if not ids:
        return set()
    return set(
        session.scalars(
            select(AtsJob.id).where(AtsJob.id.in_(ids), AtsJob.first_seen < before)
        )
    )


def close_expired(session: Session, run_at: datetime) -> None:
    session.execute(
        update(AtsJob)
        .where(
            AtsJob.source == SOURCE_HIMALAYAS,
            AtsJob.is_open.is_(True),
            or_(
                AtsJob.expires_at < run_at,
                AtsJob.last_seen < run_at - UNSEEN_CLOSE_AFTER,
            ),
        )
        .values(is_open=False)
    )


def himalayas_snapshot(
    session: Session,
    client: HimalayasClient,
    *,
    queries: tuple[str, ...] = QUERIES,
    max_pages: int = MAX_PAGES_PER_QUERY,
) -> dict[str, int]:
    run_at = datetime.now(tz=UTC)
    fetched = 0
    new = 0
    limited = False
    try:
        for query in queries:
            if limited:
                break
            for page in range(1, max_pages + 1):
                try:
                    jobs = client.search(query, page)
                except RateLimitedError:
                    logger.warning(
                        "jobtrends: Himalayas rate limit at %r page %s; stopping",
                        query,
                        page,
                    )
                    limited = True
                    break
                if not jobs:
                    break
                unique = list({j.id: j for j in jobs}.values())
                known = _known_ids(session, [j.id for j in unique], run_at)
                upsert_jobs(session, unique, run_at)
                session.commit()
                fetched += len(unique)
                fresh = [j for j in unique if j.id not in known]
                new += len(fresh)
                if not fresh:
                    break
    finally:
        session.rollback()
        close_expired(session, run_at)
        session.commit()
    logger.info("jobtrends: Himalayas — %s fetched, %s new", fetched, new)
    return {"fetched": fetched, "new": new, "rate_limited": int(limited)}
