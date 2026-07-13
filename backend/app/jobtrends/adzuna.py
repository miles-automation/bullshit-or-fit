"""Adzuna source — the big, all-industry corpus expansion.

The jobtrends ATS/board set is tech- and federal-skewed and only ~20k roles, too
small and lopsided for cross-company demand frequency to fire. Adzuna is a job-ad
AGGREGATOR with millions of live postings across EVERY industry and country, on a
free API. Pulling a broad, ops/admin-weighted sample (where the repetitive manual
work lives) turns the pool from "thousands, skewed" into "hundreds of thousands,
diverse" — exactly what the demand-mining wants.

Mirrors remote_boards.py: `parse_adzuna` is pure; `AdzunaClient` is httpx; the
snapshot upserts into the shared `ats_jobs` table (source='adzuna') and closes
roles that dropped off. No-op (skipped) until `adzuna_app_id`/`adzuna_app_key`
are configured. Free-API descriptions are truncated snippets — title + snippet +
category still carry the signal, and the VOLUME is the point.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.jobtrends.ats import (
    ParsedJob,
    _parse_dt,
    _slug_hint,
    _strip_html,
    close_missing,
    upsert_jobs,
)
from app.jobtrends.models import AtsJob

logger = logging.getLogger(__name__)

PROVIDER_ADZUNA = "adzuna"
SOURCE_ADZUNA = "adzuna"
_BASE = "https://api.adzuna.com/v1/api/jobs"
# Rows per upsert. AtsJob has ~20 columns; 500 rows = ~10k bind params, safely
# under Postgres' 65535 cap even as the per-run pull grows.
_UPSERT_CHUNK = 500

# Broad, role-type queries that span ALL industries and skew toward the
# ops/admin/coordination roles where repetitive manual work (the tooling-gap
# signal) concentrates. Each is paged, so this fans out to a large diverse pool.
_DEFAULT_QUERIES = [
    "coordinator",
    "administrator",
    "operations",
    "analyst",
    "specialist",
    "assistant",
    "clerk",
    "bookkeeper",
    "scheduler",
    "data entry",
]

# Adzuna reports annual salaries; currency follows the country.
_COUNTRY_CURRENCY = {"us": "USD", "gb": "GBP", "ca": "CAD", "au": "AUD"}


def _structured_comp(result: dict[str, Any], currency: str) -> dict[str, Any]:
    lo, hi = result.get("salary_min"), result.get("salary_max")
    vals = [int(v) for v in (lo, hi) if isinstance(v, (int, float)) and v]
    if not vals:
        return {}
    return {
        "comp_min": min(vals),
        "comp_max": max(vals),
        "comp_currency": currency,
        "comp_period": "year",
        "comp_kind": "structured",
    }


def parse_adzuna(payload: dict[str, Any], *, country: str = "us") -> list[ParsedJob]:
    currency = _COUNTRY_CURRENCY.get(country, "USD")
    jobs: list[ParsedJob] = []
    for r in payload.get("results") or []:
        if not isinstance(r, dict):
            continue
        company = str((r.get("company") or {}).get("display_name") or "").strip()
        title = _strip_html(r.get("title"))
        content = _strip_html(r.get("description"))
        if not title:
            continue
        comp = _structured_comp(r, currency)
        jobs.append(
            ParsedJob(
                source=SOURCE_ADZUNA,
                provider=PROVIDER_ADZUNA,
                company_token=_slug_hint(company) or "unknown",
                company_name=company or "Unknown",
                external_id=str(r.get("id")),
                title=title,
                location=((r.get("location") or {}).get("display_name") or None),
                department=((r.get("category") or {}).get("label") or None),
                url=r.get("redirect_url"),
                content_text=content,
                posted_at=_parse_dt(r.get("created")),
                **comp,
            )
        )
    return jobs


class AdzunaClient:
    def __init__(
        self,
        *,
        app_id: str | None = None,
        app_key: str | None = None,
        country: str | None = None,
        queries: list[str] | None = None,
        pages: int | None = None,
        results_per_page: int = 50,
        user_agent: str | None = None,
    ) -> None:
        self.app_id = app_id if app_id is not None else settings.adzuna_app_id
        self.app_key = app_key if app_key is not None else settings.adzuna_app_key
        self.country = country or settings.adzuna_country
        self.queries = queries or _DEFAULT_QUERIES
        self.pages = pages if pages is not None else settings.adzuna_pages
        self.results_per_page = results_per_page
        self.user_agent = user_agent or settings.jobtrends_user_agent

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_key)

    def _get(self, query: str, page: int) -> dict[str, Any]:
        url = f"{_BASE}/{self.country}/search/{page}"
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                url,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                params={
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "results_per_page": self.results_per_page,
                    "what": query,
                    "content-type": "application/json",
                },
            )
            resp.raise_for_status()
            return resp.json()

    def fetch_all(self) -> list[ParsedJob]:
        seen: set[str] = set()
        out: list[ParsedJob] = []
        for query in self.queries:
            for page in range(1, self.pages + 1):
                try:
                    payload = self._get(query, page)
                except Exception:  # noqa: BLE001 — one query/page must not sink the run
                    logger.exception(
                        "jobtrends: adzuna fetch failed q=%s p=%s", query, page
                    )
                    break
                batch = parse_adzuna(payload, country=self.country)
                if not batch:
                    break
                for job in batch:
                    if job.id not in seen:
                        seen.add(job.id)
                        out.append(job)
        return out


def adzuna_snapshot(
    session: Session, client: AdzunaClient | None = None
) -> dict[str, int]:
    client = client or AdzunaClient()
    if not client.configured:
        logger.info(
            "jobtrends: adzuna not configured (adzuna_app_id/app_key) — skipping"
        )
        return {"skipped": 1, "open_roles": 0}
    run_at = datetime.now(tz=UTC)
    jobs = client.fetch_all()
    # Adzuna accumulates the whole multi-query pull (thousands of rows), and
    # upsert_jobs inserts them in ONE statement — ~20 cols/row would blow Postgres'
    # 65535-parameter cap. Chunk so each upsert stays well under the limit.
    for start in range(0, len(jobs), _UPSERT_CHUNK):
        upsert_jobs(session, jobs[start : start + _UPSERT_CHUNK], run_at)
    close_missing(session, run_at, provider=PROVIDER_ADZUNA)
    session.commit()
    open_roles = session.scalar(
        select(func.count())
        .select_from(AtsJob)
        .where(AtsJob.is_open.is_(True), AtsJob.source == SOURCE_ADZUNA)
    )
    logger.info(
        "jobtrends: adzuna snapshot — %s fetched, %s open", len(jobs), open_roles
    )
    return {"skipped": 0, "open_roles": int(open_roles or 0)}
