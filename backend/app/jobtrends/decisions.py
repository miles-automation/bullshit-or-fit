from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.jobtrends.fit import normalize_company, normalize_title, url_key
from app.jobtrends.models import AtsJob, FitDecision

_LINK = re.compile(r"\]\((https?://[^)\s]+)\)|(https?://\S+)")
_DIVIDER = re.compile(r"^[\s:|-]+$")


@dataclass(frozen=True)
class TrackerRow:
    company: str
    title: str
    url: str | None
    status: str


@dataclass(frozen=True)
class SyncSummary:
    total: int
    resolved: int


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_tracker(text: str) -> list[TrackerRow]:
    rows: list[TrackerRow] = []
    header: list[str] | None = None
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            header = None
            continue
        if _DIVIDER.match(line):
            continue
        cells = _cells(line)
        if header is None:
            header = [cell.lower() for cell in cells]
            continue
        record = dict(zip(header, cells, strict=False))
        company = record.get("company", "")
        title = record.get("role", "")
        if not company or not title:
            continue
        match = _LINK.search(record.get("link", ""))
        url = (match.group(1) or match.group(2)) if match else None
        rows.append(TrackerRow(company, title, url, record.get("status", "").lower()))
    return rows


def resolve_rows(
    rows: Iterable[TrackerRow], jobs: Iterable[tuple[str, str, str | None]]
) -> list[tuple[TrackerRow, str, str, bool]]:
    by_key: dict[str, tuple[str, str]] = {}
    for company, title, url in jobs:
        key = url_key(url)
        if key is not None:
            by_key.setdefault(key, (company, title))
    resolved: list[tuple[TrackerRow, str, str, bool]] = []
    for row in rows:
        key = url_key(row.url)
        found = by_key.get(key) if key is not None else None
        company, title = found or (row.company, row.title)
        resolved.append((row, company, title, found is not None))
    return resolved


def sync_decisions(
    session: Session, rows: list[TrackerRow], now: datetime | None = None
) -> SyncSummary:
    moment = now or datetime.now(tz=UTC)
    jobs = session.execute(
        select(AtsJob.company_name, AtsJob.title, AtsJob.url).where(
            AtsJob.url.is_not(None)
        )
    ).all()
    resolved = resolve_rows(rows, [(j.company_name, j.title, j.url) for j in jobs])
    session.execute(delete(FitDecision))
    for row, company, title, _ in resolved:
        session.add(
            FitDecision(
                company_name=row.company,
                title=row.title,
                url=row.url,
                url_key=url_key(row.url),
                company_key=normalize_company(company),
                title_key=normalize_title(title),
                status=row.status or "unknown",
                synced_at=moment,
            )
        )
    session.commit()
    return SyncSummary(
        total=len(resolved), resolved=sum(1 for *_, found in resolved if found)
    )
