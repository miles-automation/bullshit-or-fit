"""jobtrends market stage: per-stream volume + demand/supply.

`extract_streams` rebuilds the derived `stream_month` table (post + author counts
per source/stream/month) straight from raw. `market_report` pivots it into the
headline multi-stream signal: job-seekers ('wants_hired') per 100 openings
('hiring') — a demand/supply read that raw posting volume alone can't give.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, distinct, func, insert, select
from sqlalchemy.orm import Session

from app.jobtrends.models import (
    STREAM_HIRING,
    STREAM_WANTS_HIRED,
    HnHiringPost,
    StreamMonth,
)

logger = logging.getLogger(__name__)


def extract_streams(session: Session) -> dict[str, int]:
    """Rebuild `stream_month` (post + distinct-author counts) from raw."""
    grouped = session.execute(
        select(
            HnHiringPost.source,
            HnHiringPost.stream,
            HnHiringPost.month,
            func.count(),
            func.count(distinct(HnHiringPost.author)),
        ).group_by(HnHiringPost.source, HnHiringPost.stream, HnHiringPost.month)
    ).all()

    rows = [
        {
            "source": source,
            "stream": stream,
            "month": month,
            "post_count": post_count,
            "author_count": author_count,
        }
        for source, stream, month, post_count, author_count in grouped
    ]

    session.execute(delete(StreamMonth))
    if rows:
        session.execute(insert(StreamMonth), rows)
    session.commit()
    logger.info("jobtrends: stream volumes rebuilt (%s stream-months)", len(rows))
    return {"stream_months": len(rows)}


@dataclass(frozen=True)
class MarketMonth:
    month: str  # 'YYYY-MM'
    hiring_posts: int
    wants_hired_posts: int
    seekers_per_100_jobs: float  # 100 * wants_hired / hiring


def market_report(session: Session) -> list[MarketMonth]:
    """Demand vs supply per month, from `stream_month`."""
    by_month: dict[date, dict[str, int]] = defaultdict(dict)
    for stream, month, post_count in session.execute(
        select(StreamMonth.stream, StreamMonth.month, StreamMonth.post_count)
    ).all():
        by_month[month][stream] = post_count

    out: list[MarketMonth] = []
    for month in sorted(by_month):
        hiring = by_month[month].get(STREAM_HIRING, 0)
        wants = by_month[month].get(STREAM_WANTS_HIRED, 0)
        ratio = (100.0 * wants / hiring) if hiring else 0.0
        out.append(
            MarketMonth(
                month=month.strftime("%Y-%m"),
                hiring_posts=hiring,
                wants_hired_posts=wants,
                seekers_per_100_jobs=round(ratio, 1),
            )
        )
    return out


def format_market_table(months: list[MarketMonth]) -> str:
    if not months:
        return "no data — run `extract` first"
    lines = [
        f"{'month':<9}{'jobs':>8}{'seekers':>10}{'seekers/100 jobs':>18}",
        "-" * 45,
    ]
    for m in months:
        lines.append(
            f"{m.month:<9}{m.hiring_posts:>8}{m.wants_hired_posts:>10}"
            f"{m.seekers_per_100_jobs:>18.0f}"
        )
    return "\n".join(lines)
