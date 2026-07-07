"""Read-only JSON API for the jobtrends hiring-trends dashboard.

These are the only routes that touch Postgres — they read the derived analysis
tables (never the raw corpus directly, never write). The landing/lead funnel
stays DB-free, so an unreachable DB degrades only this dashboard, not the site.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.jobtrends.comp import comp_trend
from app.jobtrends.recurrence import churn_report
from app.jobtrends.taxonomy import keyword_category
from app.jobtrends.trend import keyword_trend

router = APIRouter(prefix="/jobtrends", tags=["jobtrends"])


# ---- response models ------------------------------------------------------


class KeywordOption(BaseModel):
    keyword: str
    category: str


class TrendSeries(BaseModel):
    keyword: str
    category: str
    shares: list[float | None]
    mom_delta_pts: float | None


class TrendOut(BaseModel):
    months: list[str]
    series: list[TrendSeries]


class CompMonthOut(BaseModel):
    month: str
    posts_with_comp: int
    posts_total: int
    coverage_pct: float
    p25_usd: int
    median_usd: int
    p75_usd: int


class CompOut(BaseModel):
    months: list[CompMonthOut]


class CohortOut(BaseModel):
    month: str
    active: int
    new: int
    returning: int
    churned: int


class ChurnOut(BaseModel):
    distinct_authors: int
    recurring_authors: int
    recurring_pct: float
    months: list[CohortOut]


class Mover(BaseModel):
    keyword: str
    category: str
    latest_share: float
    mom_delta_pts: float


class SummaryOut(BaseModel):
    first_month: str | None
    latest_month: str | None
    months: int
    total_posts: int
    distinct_authors: int
    recurring_pct: float
    comp_coverage_pct: float
    comp_median_usd: int
    risers: list[Mover]
    fallers: list[Mover]


# ---- endpoints ------------------------------------------------------------


@router.get("/keywords", response_model=list[KeywordOption])
def list_keywords() -> list[KeywordOption]:
    """Taxonomy keywords + categories, for the dashboard's selector."""
    return [
        KeywordOption(keyword=k, category=c)
        for k, c in sorted(keyword_category().items())
    ]


@router.get("/trend", response_model=TrendOut)
def get_trend(
    keywords: str | None = Query(default=None, description="comma-separated keywords"),
    db: Session = Depends(get_db),
) -> TrendOut:
    wanted = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else None
    report = keyword_trend(db, wanted)
    cats = keyword_category()
    return TrendOut(
        months=report.months,
        series=[
            TrendSeries(
                keyword=k.keyword,
                category=cats.get(k.keyword, "other"),
                shares=[round(s, 1) if s is not None else None for s in k.shares],
                mom_delta_pts=round(k.mom_delta_pts, 1)
                if k.mom_delta_pts is not None
                else None,
            )
            for k in report.keywords
        ],
    )


@router.get("/comp", response_model=CompOut)
def get_comp(db: Session = Depends(get_db)) -> CompOut:
    return CompOut(
        months=[
            CompMonthOut(
                month=m.month,
                posts_with_comp=m.posts_with_comp,
                posts_total=m.posts_total,
                coverage_pct=round(m.coverage_pct, 1),
                p25_usd=m.p25_midpoint,
                median_usd=m.median_midpoint,
                p75_usd=m.p75_midpoint,
            )
            for m in comp_trend(db)
        ]
    )


@router.get("/churn", response_model=ChurnOut)
def get_churn(db: Session = Depends(get_db)) -> ChurnOut:
    report = churn_report(db)
    return ChurnOut(
        distinct_authors=report.distinct_authors,
        recurring_authors=report.recurring_authors,
        recurring_pct=round(report.recurring_pct, 1),
        months=[
            CohortOut(
                month=r.month.strftime("%Y-%m"),
                active=r.active_authors,
                new=r.new_authors,
                returning=r.returning_authors,
                churned=r.churned_prev,
            )
            for r in report.rows
        ],
    )


@router.get("/summary", response_model=SummaryOut)
def get_summary(db: Session = Depends(get_db)) -> SummaryOut:
    """Headline stats for the dashboard hero: coverage, recurrence, top movers."""
    comp = comp_trend(db)
    churn = churn_report(db)
    trend = keyword_trend(db, None)
    cats = keyword_category()

    total_posts = sum(m.posts_total for m in comp)
    with_comp = sum(m.posts_with_comp for m in comp)
    comp_coverage = (100.0 * with_comp / total_posts) if total_posts else 0.0
    comp_median = comp[-1].median_midpoint if comp else 0

    # Movers: keywords with the biggest MoM share change, latest month.
    movers = [
        Mover(
            keyword=k.keyword,
            category=cats.get(k.keyword, "other"),
            latest_share=round(k.shares[-1], 1)
            if k.shares and k.shares[-1] is not None
            else 0.0,
            mom_delta_pts=round(k.mom_delta_pts, 1),
        )
        for k in trend.keywords
        if k.mom_delta_pts is not None
    ]
    risers = sorted(movers, key=lambda m: m.mom_delta_pts, reverse=True)[:5]
    fallers = [
        m for m in sorted(movers, key=lambda m: m.mom_delta_pts) if m.mom_delta_pts < 0
    ][:5]

    return SummaryOut(
        first_month=comp[0].month if comp else None,
        latest_month=comp[-1].month if comp else None,
        months=len(comp),
        total_posts=total_posts,
        distinct_authors=churn.distinct_authors,
        recurring_pct=round(churn.recurring_pct, 1),
        comp_coverage_pct=round(comp_coverage, 1),
        comp_median_usd=comp_median,
        risers=risers,
        fallers=fallers,
    )
