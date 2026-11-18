from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.jobtrends.fit import normalize_company, normalize_title, url_key
from app.jobtrends.models import FitDecision

REQUIRED_COLUMNS = ("company", "role", "link", "status")
_LINK = re.compile(
    r"\]\(<?(https?://[^)>\s]+)>?\)|<(https?://[^>\s]+)>|(https?://[^\s)>]+)"
)
_DIVIDER = re.compile(r"^\|?(?:\s*:?-+:?\s*\|)+\s*:?-*:?\s*$")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


class TrackerError(ValueError):
    pass


@dataclass(frozen=True)
class TrackerRow:
    company: str
    title: str
    url: str | None
    status: str


@dataclass(frozen=True)
class SyncSummary:
    total: int
    removed: int


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.strip().replace("\\|", "|") for cell in _UNESCAPED_PIPE.split(body)]


def _link(cell: str) -> str | None:
    match = _LINK.search(cell)
    if match is None:
        return None
    return match.group(1) or match.group(2) or match.group(3)


def parse_tracker(text: str) -> list[TrackerRow]:
    rows: list[TrackerRow] = []
    header: list[str] | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.lstrip().startswith("|"):
            header = None
            continue
        if _DIVIDER.match(line.strip()):
            continue
        cells = _cells(line)
        if header is None:
            header = [cell.lower() for cell in cells]
            missing = [c for c in REQUIRED_COLUMNS if c not in header]
            if missing:
                raise TrackerError(
                    f"line {number}: table header lacks {', '.join(missing)}"
                )
            continue
        if len(cells) != len(header):
            raise TrackerError(
                f"line {number}: {len(cells)} cells, header has {len(header)}"
            )
        record = dict(zip(header, cells, strict=True))
        if not (record["company"] and record["role"] and record["status"]):
            raise TrackerError(f"line {number}: company, role and status are required")
        rows.append(
            TrackerRow(
                company=record["company"],
                title=record["role"],
                url=_link(record["link"]),
                status=record["status"].lower(),
            )
        )
    return rows


def sync_decisions(
    session: Session,
    rows: list[TrackerRow],
    *,
    allow_removals: bool = False,
    now: datetime | None = None,
) -> SyncSummary:
    moment = now or datetime.now(tz=UTC)
    session.execute(text("LOCK TABLE jobtrends.fit_decisions IN EXCLUSIVE MODE"))
    current = session.scalar(select(func.count()).select_from(FitDecision)) or 0
    removed = max(0, current - len(rows))
    if removed and not allow_removals:
        session.rollback()
        raise TrackerError(
            f"tracker has {len(rows)} rows but {current} decisions are stored; "
            "pass --allow-removals to drop decisions"
        )
    session.execute(delete(FitDecision))
    for row in rows:
        session.add(
            FitDecision(
                company_name=row.company,
                title=row.title,
                url=row.url,
                url_key=url_key(row.url),
                company_key=normalize_company(row.company),
                title_key=normalize_title(row.title),
                status=row.status,
                synced_at=moment,
            )
        )
    session.commit()
    return SyncSummary(total=len(rows), removed=removed)
