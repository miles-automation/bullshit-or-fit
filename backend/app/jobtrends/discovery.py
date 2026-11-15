from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

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
RETRY_AFTER = timedelta(days=1)
MAX_PROBES_PER_RUN = 400
MAX_REQUESTS_PER_RUN = 900
MAX_RUN_SECONDS = 30 * 60
OWNERSHIP_SAMPLE = 10

PROBE_URLS: dict[str, str] = {
    PROVIDER_ASHBY: "https://api.ashbyhq.com/posting-api/job-board/{token}",
    PROVIDER_GREENHOUSE: "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
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


def board_jobs(provider: str, payload: Any) -> list[dict[str, Any]] | None:
    if provider == PROVIDER_LEVER:
        jobs = payload if isinstance(payload, list) else None
    elif isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
        jobs = payload["jobs"]
    else:
        jobs = None
    if jobs is None or not all(isinstance(j, dict) for j in jobs):
        return None
    return jobs


_ATS_URL = re.compile(r"https?://\S*(ashbyhq\.com|greenhouse\.io|lever\.co)\S*", re.I)


def _job_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_job_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_job_text(v) for v in value)
    if isinstance(value, str):
        return _ATS_URL.sub(" ", value)
    return ""


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _domain(website: str | None) -> str | None:
    if not website:
        return None
    host = urlparse(website if "//" in website else f"//{website}").hostname or ""
    host = host.lower().removeprefix("www.")
    return host or None


def owns_board(candidate: Candidate, jobs: list[Any]) -> bool:
    name = _normalize(candidate.name)
    domain = _domain(candidate.website)
    for job in jobs[:OWNERSHIP_SAMPLE]:
        raw = _job_text(job).lower()
        if domain and domain in raw:
            return True
        if name and f" {name} " in f" {_normalize(raw)} ":
            return True
    return False


@dataclass(frozen=True)
class ProbeResult:
    hit: BoardHit | None
    conclusive: bool


class BoardProber:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        pause_seconds: float = 0.2,
        max_requests: int = MAX_REQUESTS_PER_RUN,
        max_seconds: float = MAX_RUN_SECONDS,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={"User-Agent": settings.jobtrends_user_agent},
        )
        self._pause = pause_seconds
        self._sleep = sleep
        self._clock = clock
        self._max_requests = max_requests
        self._deadline = clock() + max_seconds
        self.requests = 0

    @property
    def exhausted(self) -> bool:
        return self.requests >= self._max_requests or self._clock() >= self._deadline

    def close(self) -> None:
        self._client.close()

    def fetch_yc_hiring(self) -> Any:
        resp = self._client.get(YC_HIRING_URL, timeout=60.0)
        resp.raise_for_status()
        return resp.json()

    def probe(
        self, candidate: Candidate, stored: tuple[str, str] | None = None
    ) -> ProbeResult:
        attempts: list[tuple[str, str]] = [stored] if stored else []
        for token in candidate_tokens(candidate):
            for provider in PROBE_ORDER:
                if (provider, token) not in attempts:
                    attempts.append((provider, token))
        conclusive = True
        for provider, token in attempts:
            if self.exhausted:
                return ProbeResult(None, False)
            self._sleep(self._pause)
            self.requests += 1
            try:
                resp = self._client.get(PROBE_URLS[provider].format(token=token))
            except httpx.HTTPError:
                conclusive = False
                continue
            if resp.status_code == 404:
                continue
            if resp.status_code != 200:
                conclusive = False
                continue
            try:
                jobs = board_jobs(provider, resp.json())
            except ValueError:
                conclusive = False
                continue
            if jobs is None:
                conclusive = False
                continue
            if (provider, token) == stored or owns_board(candidate, jobs):
                return ProbeResult(BoardHit(provider, token, len(jobs)), True)
        return ProbeResult(None, conclusive)


@dataclass(frozen=True)
class ProbeState:
    probed_at: datetime | None
    attempted_at: datetime | None


def due_candidates(
    candidates: list[Candidate],
    state: dict[str, ProbeState],
    now: datetime,
    limit: int = MAX_PROBES_PER_RUN,
) -> list[Candidate]:
    never: list[Candidate] = []
    stale: list[tuple[datetime, Candidate]] = []
    for c in candidates:
        st = state.get(c.slug)
        if st is None or (st.probed_at is None and st.attempted_at is None):
            never.append(c)
            continue
        if st.probed_at is not None and st.probed_at >= now - REPROBE_AFTER:
            continue
        if st.attempted_at is not None and st.attempted_at >= now - RETRY_AFTER:
            continue
        last = max(t for t in (st.probed_at, st.attempted_at) if t is not None)
        stale.append((last, c))
    stale.sort(key=lambda item: item[0])
    return (never + [c for _, c in stale])[:limit]


def _mapping(row: DiscoveredBoard | None) -> tuple[str, str] | None:
    if row is None or not row.found or row.provider is None or row.token is None:
        return None
    return (row.provider, row.token)


def _record_attempt(
    session: Session, origin: str, candidate: Candidate, run_at: datetime
) -> None:
    stmt = pg_insert(DiscoveredBoard).values(
        origin=origin,
        company_slug=candidate.slug,
        company_name=candidate.name,
        team_size=candidate.team_size,
        website=candidate.website,
        found=False,
        attempted_at=run_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[DiscoveredBoard.origin, DiscoveredBoard.company_slug],
        set_={"attempted_at": stmt.excluded.attempted_at},
    )
    session.execute(stmt)
    session.commit()


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
    seeded = {(c.provider, c.token) for c in SEED_COMPANIES}
    existing = {
        row.company_slug: row
        for row in session.scalars(
            select(DiscoveredBoard).where(DiscoveredBoard.origin == origin)
        )
    }
    eligible = {c.slug for c in candidates}
    pool = candidates + [
        Candidate(row.company_slug, row.company_name, row.team_size, row.website)
        for row in existing.values()
        if row.found and row.company_slug not in eligible
    ]
    state = {
        slug: ProbeState(row.probed_at, row.attempted_at)
        for slug, row in existing.items()
    }
    due = due_candidates(pool, state, run_at, limit)
    probed = found = lost = inconclusive = 0
    for candidate in due:
        if prober.exhausted:
            break
        old = _mapping(existing.get(candidate.slug))
        try:
            result = prober.probe(candidate, old)
        except Exception:  # noqa: BLE001
            logger.exception("jobtrends: board probe failed for %s", candidate.slug)
            result = ProbeResult(None, False)
        if not result.conclusive:
            _record_attempt(session, origin, candidate, run_at)
            inconclusive += 1
            continue
        hit = result.hit
        new = (hit.provider, hit.token) if hit else None
        if old is not None and old != new:
            if old not in seeded:
                close_missing(
                    session,
                    run_at,
                    provider=old[0],
                    company_token=old[1],
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
            attempted_at=run_at,
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
                "attempted_at": stmt.excluded.attempted_at,
            },
        )
        session.execute(stmt)
        session.commit()
        probed += 1
        if hit is not None:
            found += 1
    logger.info(
        "jobtrends: board discovery — probed %s, found %s, lost %s, inconclusive %s",
        probed,
        found,
        lost,
        inconclusive,
    )
    return {
        "probed": probed,
        "found": found,
        "lost": lost,
        "inconclusive": inconclusive,
    }


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
    result = {"probed": 0, "found": 0, "lost": 0, "inconclusive": 0}
    try:
        candidates = parse_yc_hiring(prober.fetch_yc_hiring())
        result = discover_boards(session, prober, candidates)
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("jobtrends: YC discovery failed; snapshotting known boards")
    companies = discovered_companies(session)
    snap = ats_snapshot(session, ats_client, companies)
    return {**result, "candidates": len(candidates), "boards": len(companies), **snap}
