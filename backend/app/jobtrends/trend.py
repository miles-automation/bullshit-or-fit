"""jobtrends trend stage: month x keyword share-of-postings, with MoM delta.

Reads the derived `keyword_month_stats` table (never the raw corpus directly) and
pivots it into a share-of-postings time series per keyword. `keyword_trend`
returns structured data (ready for a future API/UI); `format_trend_table` renders
the reference script's terminal view.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobtrends.models import KeywordMonthStat
from app.jobtrends.taxonomy import flat_keywords


@dataclass(frozen=True)
class KeywordTrend:
    keyword: str
    # share (0-100) per month, aligned to `months`; None where that month has no data.
    shares: list[float | None]
    mom_delta_pts: float | None  # last month's share minus the prior month's


@dataclass(frozen=True)
class TrendReport:
    months: list[str]  # 'YYYY-MM', ascending
    keywords: list[KeywordTrend]


def keyword_trend(session: Session, keywords: list[str] | None = None) -> TrendReport:
    """Share-of-postings (%) per keyword per month, plus month-over-month delta.

    `keywords` filters/orders the output; None = every taxonomy keyword.
    """
    wanted = keywords or list(flat_keywords())

    rows = session.execute(
        select(
            KeywordMonthStat.month,
            KeywordMonthStat.keyword,
            KeywordMonthStat.posts_matched,
            KeywordMonthStat.posts_total,
        )
    ).all()

    months = sorted({r.month for r in rows})
    month_index = {m: i for i, m in enumerate(months)}
    # keyword -> [share per month | None]
    by_kw: dict[str, list[float | None]] = {}
    for r in rows:
        if r.keyword not in wanted:
            continue
        series = by_kw.setdefault(r.keyword, [None] * len(months))
        share = (100.0 * r.posts_matched / r.posts_total) if r.posts_total else 0.0
        series[month_index[r.month]] = share

    trends: list[KeywordTrend] = []
    for kw in wanted:
        shares = by_kw.get(kw, [None] * len(months))
        mom = None
        if len(shares) >= 2 and shares[-1] is not None and shares[-2] is not None:
            mom = shares[-1] - shares[-2]
        trends.append(KeywordTrend(keyword=kw, shares=shares, mom_delta_pts=mom))

    return TrendReport(months=[m.strftime("%Y-%m") for m in months], keywords=trends)


def format_trend_table(report: TrendReport) -> str:
    """Render a TrendReport as the reference script's terminal table."""
    if not report.months:
        return "no data — run `extract` first (and `ingest` before that)"

    width = max((len(k.keyword) for k in report.keywords), default=7) + 1
    header = (
        "keyword".ljust(width)
        + "".join(m[2:].rjust(9) for m in report.months)
        + "    MoM"
    )
    lines = [header, "-" * len(header)]
    for kt in report.keywords:
        cells = "".join(
            (f"{s:8.0f}%" if s is not None else " " * 8 + "-") for s in kt.shares
        )
        mom = (
            f"  {kt.mom_delta_pts:+5.0f}pt"
            if kt.mom_delta_pts is not None
            else "      -"
        )
        lines.append(kt.keyword.ljust(width) + cells + mom)
    return "\n".join(lines)
