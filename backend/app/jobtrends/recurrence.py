"""jobtrends recurrence stage: who keeps hiring vs. who drops off.

Companies repost the same listing month after month; the interesting signal is the
churn around that — new entrants, returning repeats, and the cohort that was
hiring last month but went quiet. `compute_cohorts` is a pure function over
(author, month) pairs; `extract_cohorts` rebuilds the derived `cohort_month`
table from raw, and `churn_report` reads it back.

Author = the HN account that posted. For most companies that's a stable handle,
so it's a decent (not perfect) company proxy — good enough for cohort trends.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.jobtrends.models import STREAM_HIRING, CohortMonth, HnHiringPost

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CohortRow:
    month: date
    active_authors: int
    new_authors: int  # first-ever appearance this month
    returning_authors: int  # active AND seen in some earlier month
    churned_prev: int  # active last month, absent this month


def compute_cohorts(pairs: Iterable[tuple[str, date]]) -> list[CohortRow]:
    """Cohort stats per month from (author, month) presence pairs. Pure."""
    by_month: dict[date, set[str]] = {}
    for author, month in pairs:
        if not author:
            continue
        by_month.setdefault(month, set()).add(author)

    seen: set[str] = set()
    prev_active: set[str] = set()
    out: list[CohortRow] = []
    for month in sorted(by_month):
        active = by_month[month]
        new = active - seen
        returning = active & seen
        churned = prev_active - active
        out.append(
            CohortRow(
                month=month,
                active_authors=len(active),
                new_authors=len(new),
                returning_authors=len(returning),
                churned_prev=len(churned),
            )
        )
        seen |= active
        prev_active = active
    return out


def extract_cohorts(session: Session) -> dict[str, int]:
    """Rebuild `cohort_month` from raw. Returns {months, authors}."""
    pairs = [
        (author, month)
        for author, month in session.execute(
            select(HnHiringPost.author, HnHiringPost.month)
            .where(HnHiringPost.stream == STREAM_HIRING)
            .distinct()
        ).all()
        if author
    ]
    cohorts = compute_cohorts(pairs)

    session.execute(delete(CohortMonth))
    if cohorts:
        session.execute(
            insert(CohortMonth),
            [
                {
                    "month": c.month,
                    "active_authors": c.active_authors,
                    "new_authors": c.new_authors,
                    "returning_authors": c.returning_authors,
                    "churned_prev": c.churned_prev,
                }
                for c in cohorts
            ],
        )
    session.commit()
    distinct_authors = len({a for a, _ in pairs})
    logger.info(
        "jobtrends: cohorts rebuilt — %s months, %s distinct authors",
        len(cohorts),
        distinct_authors,
    )
    return {"months": len(cohorts), "authors": distinct_authors}


@dataclass(frozen=True)
class ChurnReport:
    rows: list[CohortRow]
    distinct_authors: int
    recurring_authors: int  # posted in >= 2 months
    recurring_pct: float


def churn_report(session: Session) -> ChurnReport:
    rows = [
        CohortRow(
            month=r.month,
            active_authors=r.active_authors,
            new_authors=r.new_authors,
            returning_authors=r.returning_authors,
            churned_prev=r.churned_prev,
        )
        for r in session.execute(
            select(CohortMonth).order_by(CohortMonth.month)
        ).scalars()
    ]

    # Recurring = authors appearing in >= 2 distinct months (computed from raw).
    counts: dict[str, int] = {}
    for author, month in session.execute(
        select(HnHiringPost.author, HnHiringPost.month)
        .where(HnHiringPost.stream == STREAM_HIRING)
        .distinct()
    ).all():
        if author:
            counts[author] = counts.get(author, 0) + 1
    distinct = len(counts)
    recurring = sum(1 for n in counts.values() if n >= 2)
    return ChurnReport(
        rows=rows,
        distinct_authors=distinct,
        recurring_authors=recurring,
        recurring_pct=(100.0 * recurring / distinct) if distinct else 0.0,
    )


def format_churn_table(report: ChurnReport) -> str:
    if not report.rows:
        return "no data — run `extract` first"
    lines = [
        f"{report.recurring_authors}/{report.distinct_authors} authors recur "
        f"(>=2 months) = {report.recurring_pct:.0f}%",
        "",
        f"{'month':<9}{'active':>8}{'new':>8}{'returning':>11}{'churned':>9}",
        "-" * 45,
    ]
    for r in report.rows:
        lines.append(
            f"{r.month.strftime('%Y-%m'):<9}{r.active_authors:>8}{r.new_authors:>8}"
            f"{r.returning_authors:>11}{r.churned_prev:>9}"
        )
    return "\n".join(lines)
