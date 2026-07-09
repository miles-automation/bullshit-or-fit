"""Workday CXS source — the big established employers the token-boards miss.

Most large Front-Range employers (Broadcom, HPE, Advanced Energy, Comcast) don't
run Greenhouse/Lever/Ashby; they run Workday. Workday exposes an unauthenticated
"CXS" JSON search API per tenant:

    POST https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    body: {"appliedFacets":{}, "limit":20, "offset":N, "searchText":"<hint>"}

The list response gives title + externalPath + locationsText per posting but NO
description or comp. That's enough for the commute-shed radar (which only needs
title / location / link), so we keep it list-only — no per-job detail fetch.

`searchText` is a coarse server-side pre-filter (a city name); the authoritative
in-reach decision is still made by the caller via `role_in_shed` on the location
parsed out of `externalPath` (its first segment is the posting's primary-location
slug, e.g. `/job/Fort-Collins-Colorado/...` or `/job/USA-Colorado-Fort-Collins-.../`).

`parse_workday` is pure (JSON -> ParsedJob[]) and unit-testable; the client is a
thin httpx wrapper with an injectable transport for tests.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from app.config import settings
from app.jobtrends.ats import ParsedJob

logger = logging.getLogger(__name__)

PROVIDER_WORKDAY = "workday"

# Safety cap on pagination: searchText already narrows to a metro, so a handful of
# pages is plenty; this just stops a runaway loop on a huge/misconfigured board.
_PAGE_SIZE = 20
_MAX_PAGES = 8


@dataclass(frozen=True)
class WorkdayConfig:
    """Per-employer Workday coordinates + a location pre-filter hint."""

    tenant: str  # e.g. "hpe"
    host: str  # the wd shard, e.g. "wd5"
    site: str  # career-site id, e.g. "Jobsathpe"
    search: str = ""  # searchText pre-filter (a city name), coarse


def _base_url(cfg: WorkdayConfig) -> str:
    return f"https://{cfg.tenant}.{cfg.host}.myworkdayjobs.com"


def _location_from_path(external_path: str) -> str | None:
    """The first path segment after /job/ is the primary-location slug; turn it
    back into a plain string for `role_in_shed` (e.g. 'Fort-Collins-Colorado' ->
    'Fort Collins Colorado')."""
    m = re.match(r"/job/([^/]+)/", external_path or "")
    return m.group(1).replace("-", " ") if m else None


def parse_workday(
    company_token: str, company_name: str, cfg: WorkdayConfig, payload: dict
) -> list[ParsedJob]:
    """Workday CXS `jobs` JSON -> ParsedJob[]. Pure. Location comes from the
    externalPath slug; there's no description/comp in the list response."""
    site_base = f"{_base_url(cfg)}/en-US/{cfg.site}"
    jobs: list[ParsedJob] = []
    for j in payload.get("jobPostings") or []:
        path = j.get("externalPath") or ""
        if not path:
            continue
        title = str(j.get("title") or "").strip()
        location = _location_from_path(path) or (j.get("locationsText") or None)
        jobs.append(
            ParsedJob(
                provider=PROVIDER_WORKDAY,
                company_token=company_token,
                company_name=company_name,
                # externalPath is stable + unique per posting → the id key.
                external_id=path,
                title=title,
                location=location,
                department=None,
                url=f"{site_base}{path}",
                content_text=title,  # list API carries no body
                posted_at=None,  # only a relative "Posted N Days Ago" string
            )
        )
    return jobs


class WorkdayClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        # Workday edge tends to reject an empty/py-httpx UA; use the configured one.
        self._user_agent = user_agent or settings.jobtrends_user_agent

    def fetch(
        self, company_token: str, company_name: str, cfg: WorkdayConfig
    ) -> list[ParsedJob]:
        """Paginate the CXS search (bounded) and parse every page. The caller still
        filters the returned rows to those actually in reach."""
        url = f"{_base_url(cfg)}/wday/cxs/{cfg.tenant}/{cfg.site}/jobs"
        headers = {
            "User-Agent": self._user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        out: list[ParsedJob] = []
        with httpx.Client(
            timeout=self.timeout_seconds, transport=self._transport
        ) as client:
            for page in range(_MAX_PAGES):
                body = {
                    "appliedFacets": {},
                    "limit": _PAGE_SIZE,
                    "offset": page * _PAGE_SIZE,
                    "searchText": cfg.search,
                }
                resp = client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                payload = resp.json()
                postings = payload.get("jobPostings") or []
                if not postings:
                    break
                out.extend(parse_workday(company_token, company_name, cfg, payload))
                total = payload.get("total")
                if isinstance(total, int) and (page + 1) * _PAGE_SIZE >= total:
                    break
        return out
