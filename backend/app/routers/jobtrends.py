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
from app.jobtrends.ats import ats_report
from app.jobtrends.comp import comp_sources, comp_trend
from app.jobtrends.geo import geo_report
from app.jobtrends.market import market_report
from app.jobtrends.recurrence import churn_report
from app.jobtrends.remote_boards import remote_report
from app.jobtrends.skill_comp import skill_comp
from app.jobtrends.skill_demand import skill_demand
from app.jobtrends.taxonomy import keyword_category
from app.jobtrends.trend import keyword_trend
from app.jobtrends.usajobs import usajobs_report

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


class CompSourceOut(BaseModel):
    source: str
    n_roles: int
    n_with_comp: int
    coverage_pct: float
    p25_usd: int
    median_usd: int
    p75_usd: int


class CompSourcesOut(BaseModel):
    sources: list[CompSourceOut]


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


class MarketMonthOut(BaseModel):
    month: str
    hiring_posts: int
    wants_hired_posts: int
    seekers_per_100_jobs: float


class MarketOut(BaseModel):
    months: list[MarketMonthOut]


class CompanyOpeningsOut(BaseModel):
    company_name: str
    company_token: str
    open_roles: int


class CompaniesOut(BaseModel):
    total_open: int
    companies: int
    top: list[CompanyOpeningsOut]


class NameCountOut(BaseModel):
    name: str
    open_roles: int


class RemoteOut(BaseModel):
    total_open: int
    companies: int
    top_companies: list[NameCountOut]
    top_categories: list[NameCountOut]


class UsaJobsOut(BaseModel):
    total_open: int
    agencies: int
    top_agencies: list[NameCountOut]
    top_categories: list[NameCountOut]


class SkillOut(BaseModel):
    keyword: str
    category: str
    roles_matched: int
    share: float
    by_source: dict[str, float]


class SkillsOut(BaseModel):
    total_roles: int
    sources: list[str]
    skills: list[SkillOut]


class MetroCountOut(BaseModel):
    metro: str
    n_roles: int


class GeoSourceOut(BaseModel):
    source: str
    total: int
    remote_pct: float
    top_metros: list[MetroCountOut]


class GeoOut(BaseModel):
    sources: list[GeoSourceOut]


class SkillCompCellOut(BaseModel):
    n_with_comp: int
    p25_usd: int
    median_usd: int
    p75_usd: int


class SkillCompRowOut(BaseModel):
    keyword: str
    category: str
    total_n: int
    by_source: dict[str, SkillCompCellOut]


class SkillCompOut(BaseModel):
    sources: list[str]
    skills: list[SkillCompRowOut]


class SummaryOut(BaseModel):
    first_month: str | None
    latest_month: str | None
    months: int
    total_posts: int
    distinct_authors: int
    recurring_pct: float
    comp_coverage_pct: float
    comp_median_usd: int
    seekers_per_100_jobs: float  # latest month; 0 if no candidate data
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


@router.get("/comp/sources", response_model=CompSourcesOut)
def get_comp_sources(db: Session = Depends(get_db)) -> CompSourcesOut:
    """Pay quartiles per source on one comparable USD axis (HN vs companies vs
    remote vs federal)."""
    return CompSourcesOut(
        sources=[
            CompSourceOut(
                source=r.source,
                n_roles=r.n_roles,
                n_with_comp=r.n_with_comp,
                coverage_pct=r.coverage_pct,
                p25_usd=r.p25_usd,
                median_usd=r.median_usd,
                p75_usd=r.p75_usd,
            )
            for r in comp_sources(db)
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


@router.get("/market", response_model=MarketOut)
def get_market(db: Session = Depends(get_db)) -> MarketOut:
    return MarketOut(
        months=[
            MarketMonthOut(
                month=m.month,
                hiring_posts=m.hiring_posts,
                wants_hired_posts=m.wants_hired_posts,
                seekers_per_100_jobs=m.seekers_per_100_jobs,
            )
            for m in market_report(db)
        ]
    )


@router.get("/companies", response_model=CompaniesOut)
def get_companies(db: Session = Depends(get_db)) -> CompaniesOut:
    """Currently-open roles per company, from the latest ATS snapshot."""
    report = ats_report(db)
    return CompaniesOut(
        total_open=report.total_open,
        companies=report.companies,
        top=[
            CompanyOpeningsOut(
                company_name=c.company_name,
                company_token=c.company_token,
                open_roles=c.open_roles,
            )
            for c in report.top
        ],
    )


@router.get("/remote", response_model=RemoteOut)
def get_remote(db: Session = Depends(get_db)) -> RemoteOut:
    """Remote job market — open roles by category/company (Remotive + RemoteOK)."""
    report = remote_report(db)
    return RemoteOut(
        total_open=report.total_open,
        companies=report.companies,
        top_companies=[
            NameCountOut(name=c.name, open_roles=c.open_roles)
            for c in report.top_companies
        ],
        top_categories=[
            NameCountOut(name=c.name, open_roles=c.open_roles)
            for c in report.top_categories
        ],
    )


@router.get("/usajobs", response_model=UsaJobsOut)
def get_usajobs(db: Session = Depends(get_db)) -> UsaJobsOut:
    """Federal job market — open roles by agency/category (USAJobs)."""
    report = usajobs_report(db)
    return UsaJobsOut(
        total_open=report.total_open,
        agencies=report.agencies,
        top_agencies=[
            NameCountOut(name=a.name, open_roles=a.open_roles)
            for a in report.top_agencies
        ],
        top_categories=[
            NameCountOut(name=c.name, open_roles=c.open_roles)
            for c in report.top_categories
        ],
    )


@router.get("/skills", response_model=SkillsOut)
def get_skills(db: Session = Depends(get_db)) -> SkillsOut:
    """Cross-source skill demand across live ATS/remote/federal openings."""
    report = skill_demand(db)
    return SkillsOut(
        total_roles=report.total_roles,
        sources=report.sources,
        skills=[
            SkillOut(
                keyword=s.keyword,
                category=s.category,
                roles_matched=s.roles_matched,
                share=s.share,
                by_source=s.by_source,
            )
            for s in report.skills
        ],
    )


@router.get("/locations", response_model=GeoOut)
def get_locations(db: Session = Depends(get_db)) -> GeoOut:
    """Where the open roles are: top metros + remote share, per source."""
    return GeoOut(
        sources=[
            GeoSourceOut(
                source=g.source,
                total=g.total,
                remote_pct=g.remote_pct,
                top_metros=[
                    MetroCountOut(metro=m.metro, n_roles=m.n_roles)
                    for m in g.top_metros
                ],
            )
            for g in geo_report(db)
        ]
    )


@router.get("/skills/comp", response_model=SkillCompOut)
def get_skill_comp(db: Session = Depends(get_db)) -> SkillCompOut:
    """Median advertised pay per skill, compared across sources."""
    report = skill_comp(db)
    return SkillCompOut(
        sources=report.sources,
        skills=[
            SkillCompRowOut(
                keyword=s.keyword,
                category=s.category,
                total_n=s.total_n,
                by_source={
                    src: SkillCompCellOut(
                        n_with_comp=c.n_with_comp,
                        p25_usd=c.p25_usd,
                        median_usd=c.median_usd,
                        p75_usd=c.p75_usd,
                    )
                    for src, c in s.by_source.items()
                },
            )
            for s in report.skills
        ],
    )


@router.get("/summary", response_model=SummaryOut)
def get_summary(db: Session = Depends(get_db)) -> SummaryOut:
    """Headline stats for the dashboard hero: coverage, recurrence, top movers."""
    comp = comp_trend(db)
    churn = churn_report(db)
    trend = keyword_trend(db, None)
    market = market_report(db)
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
        seekers_per_100_jobs=market[-1].seekers_per_100_jobs if market else 0.0,
        risers=risers,
        fallers=fallers,
    )
