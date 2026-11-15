from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.jobtrends.ats import (
    PROVIDER_ASHBY,
    PROVIDER_GREENHOUSE,
    PROVIDER_LEVER,
    SEED_COMPANIES,
    SOURCE_ATS,
    AtsClient,
    Company,
    ats_snapshot,
    close_missing,
)
from app.jobtrends.models import DiscoveredBoard

logger = logging.getLogger(__name__)

ORIGIN_YC = "yc"
YC_HIRING_URL = "https://yc-oss.github.io/api/companies/hiring.json"
MAX_TEAM_SIZE = 100
REPROBE_AFTER = timedelta(days=7)
MAX_PROBES_PER_RUN = 400

PROBE_URLS: dict[str, str] = {
    PROVIDER_ASHBY: "https://api.ashbyhq.com/posting-api/job-board/{token}",
    PROVIDER_GREENHOUSE: "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    PROVIDER_LEVER: "https://api.lever.co/v0/postings/{token}?mode=json",
}
PROBE_ORDER = (PROVIDER_ASHBY, PROVIDER_GREENHOUSE, PROVIDER_LEVER)


@dataclass(frozen=True)
class Candidate:
    slug: str
    name: str
    team_size: int | None
    website: str | None


@dataclass(frozen=True)
class BoardHit:
    provider: str
    token: str
    open_roles: int


def parse_yc_hiring(
    payload: Any, max_team_size: int = MAX_TEAM_SIZE
) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set()
    for c in payload if isinstance(payload, list) else []:
        if not isinstance(c, dict):
            continue
        slug = str(c.get("slug") or "").strip().lower()
        name = str(c.get("name") or "").strip()
        if not slug or not name or slug in seen:
            continue
        if c.get("status") != "Active" or c.get("isHiring") is False:
            continue
        size = c.get("team_size")
        team_size = size if isinstance(size, int) and size > 0 else None
        if team_size is None or team_size > max_team_size:
            continue
        seen.add(slug)
        out.append(
            Candidate(
                slug=slug,
                name=name,
                team_size=team_size,
                website=(str(c.get("website")) if c.get("website") else None),
            )
        )
    return out


def candidate_tokens(candidate: Candidate) -> list[str]:
    tokens: list[str] = []
    for t in (
        candidate.slug,
        candidate.slug.replace("-", ""),
        re.sub(r"[^a-z0-9]", "", candidate.name.lower()),
    ):
        if t and t not in tokens:
            tokens.append(t)
    return tokens


def board_open_roles(provider: str, payload: Any) -> int | None:
    if provider == PROVIDER_LEVER:
        return len(payload) if isinstance(payload, list) else None
    if isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
        return len(payload["jobs"])
    return None


class BoardProber:
    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        pause_seconds: float = 0.2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={"User-Agent": settings.jobtrends_user_agent},
        )
        self._pause = pause_seconds
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def fetch_yc_hiring(self) -> Any:
        resp = self._client.get(YC_HIRING_URL, timeout=60.0)
        resp.raise_for_status()
        return resp.json()

    def probe(self, candidate: Candidate) -> BoardHit | None:
        for token in candidate_tokens(candidate):
            for provider in PROBE_ORDER:
                self._sleep(self._pause)
                try:
                    resp = self._client.get(PROBE_URLS[provider].format(token=token))
                except httpx.HTTPError:
                    continue
                if resp.status_code != 200:
                    continue
                try:
                    count = board_open_roles(provider, resp.json())
                except ValueError:
                    continue
                if count is not None:
                    return BoardHit(provider=provider, token=token, open_roles=count)
        return None


def due_candidates(
    candidates: list[Candidate],
    probed_at: dict[str, datetime],
    now: datetime,
    limit: int = MAX_PROBES_PER_RUN,
) -> list[Candidate]:
    never = [c for c in candidates if c.slug not in probed_at]
    stale = sorted(
        (
            c
            for c in candidates
            if c.slug in probed_at and probed_at[c.slug] < now - REPROBE_AFTER
        ),
        key=lambda c: probed_at[c.slug],
    )
    return (never + stale)[:limit]


def discover_boards(
    session: Session,
    prober: BoardProber,
    candidates: list[Candidate],
    *,
    origin: str = ORIGIN_YC,
    now: datetime | None = None,
    limit: int = MAX_PROBES_PER_RUN,
) -> dict[str, int]:
    run_at = now or datetime.now(tz=UTC)
    existing = {
        row.company_slug: row
        for row in session.scalars(
            select(DiscoveredBoard).where(DiscoveredBoard.origin == origin)
        )
    }
    probed_at = {slug: row.probed_at for slug, row in existing.items()}
    due = due_candidates(candidates, probed_at, run_at, limit)
    found = 0
    lost = 0
    for candidate in due:
        try:
            hit = prober.probe(candidate)
        except Exception:  # noqa: BLE001
            logger.exception("jobtrends: board probe failed for %s", candidate.slug)
            continue
        previous = existing.get(candidate.slug)
        if hit is None and previous is not None and previous.found:
            if previous.provider is not None and previous.token is not None:
                close_missing(
                    session,
                    run_at,
                    provider=previous.provider,
                    company_token=previous.token,
                    source=SOURCE_ATS,
                )
            lost += 1
        stmt = pg_insert(DiscoveredBoard).values(
            origin=origin,
            company_slug=candidate.slug,
            company_name=candidate.name,
            team_size=candidate.team_size,
            website=candidate.website,
            provider=hit.provider if hit else None,
            token=hit.token if hit else None,
            found=hit is not None,
            probed_at=run_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DiscoveredBoard.origin, DiscoveredBoard.company_slug],
            set_={
                "company_name": stmt.excluded.company_name,
                "team_size": stmt.excluded.team_size,
                "website": stmt.excluded.website,
                "provider": stmt.excluded.provider,
                "token": stmt.excluded.token,
                "found": stmt.excluded.found,
                "probed_at": stmt.excluded.probed_at,
            },
        )
        session.execute(stmt)
        session.commit()
        if hit is not None:
            found += 1
    logger.info(
        "jobtrends: board discovery — probed %s, found %s, lost %s",
        len(due),
        found,
        lost,
    )
    return {"probed": len(due), "found": found, "lost": lost}


def discovered_companies(session: Session) -> list[Company]:
    seeded = {(c.provider, c.token) for c in SEED_COMPANIES}
    rows = session.execute(
        select(
            DiscoveredBoard.company_name,
            DiscoveredBoard.provider,
            DiscoveredBoard.token,
        )
        .where(DiscoveredBoard.found.is_(True))
        .order_by(DiscoveredBoard.company_slug)
    ).all()
    out: list[Company] = []
    taken: set[tuple[str, str]] = set(seeded)
    for name, provider, token in rows:
        if provider is None or token is None or (provider, token) in taken:
            continue
        taken.add((provider, token))
        out.append(Company(name, provider, token))
    return out


def discovery_tick(
    session: Session,
    prober: BoardProber,
    ats_client: AtsClient,
) -> dict[str, int]:
    candidates: list[Candidate] = []
    result = {"probed": 0, "found": 0, "lost": 0}
    try:
        candidates = parse_yc_hiring(prober.fetch_yc_hiring())
        result = discover_boards(session, prober, candidates)
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("jobtrends: YC discovery failed; snapshotting known boards")
    companies = discovered_companies(session)
    snap = ats_snapshot(session, ats_client, companies)
    return {**result, "candidates": len(candidates), "boards": len(companies), **snap}
