"""Market-fit engine — the shared core behind both "bullshit or fit?" faces.

Given a claimed profile (skills + seniority + comp), it reads the live market and
returns a neutral assessment: the market comp range for that profile, each skill's
demand + trajectory, rising skills the profile lacks, live roles that match, and a
comp verdict (under / fit / over the market band).

Both product faces call this and only differ in framing:
- Seeker: "are your comp expectations a fit for the market?"
- Employer: "is this candidate's claimed comp/skillset bullshit or a fit?"

Comp + live-role benchmarks come from the live *private-sector* openings (company
boards + remote aggregators). Federal (USAJobs) is excluded — it's a different pay
scale and hiring process, and would drag a tech profile's benchmark down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobtrends.ats import SOURCE_ATS, SOURCE_REMOTE
from app.jobtrends.extract import compile_taxonomy
from app.jobtrends.models import AtsJob
from app.jobtrends.taxonomy import keyword_category
from app.jobtrends.trend import keyword_trend

# Categories that are genuine skills (vs. role/seniority modifiers) — used when
# suggesting gaps to learn.
_SKILL_CATEGORIES = {"language", "framework", "data", "infra", "ai"}

_PROFILE_SOURCES = (SOURCE_ATS, SOURCE_REMOTE)


def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


@dataclass(frozen=True)
class SkillSignal:
    skill: str
    category: str
    demand_share: float  # % of live roles mentioning it (latest)
    mom_delta_pts: float | None
    trajectory: str  # 'rising' | 'flat' | 'falling'


@dataclass(frozen=True)
class RoleMatch:
    company: str
    title: str
    source: str
    skills_matched: int
    comp_median_usd: int | None


@dataclass(frozen=True)
class MarketFit:
    skills: list[str]
    seniority: str | None
    comp_usd: int | None
    # comp benchmark for the profile
    comp_n: int
    comp_p25_usd: int
    comp_median_usd: int
    comp_p75_usd: int
    comp_verdict: str  # 'under' | 'fit' | 'over' | 'unknown'
    comp_delta_pct: float | None  # claimed vs market median, signed %
    # skill signals + gaps
    skill_signals: list[SkillSignal]
    gaps: list[SkillSignal]  # rising skills the profile lacks
    # live demand
    matching_roles: int
    top_roles: list[RoleMatch]


def _trajectory(mom: float | None) -> str:
    if mom is None:
        return "flat"
    if mom >= 0.5:
        return "rising"
    if mom <= -0.5:
        return "falling"
    return "flat"


def evaluate(
    session: Session,
    *,
    skills: list[str],
    seniority: str | None = None,
    comp_usd: int | None = None,
) -> MarketFit:
    patterns = compile_taxonomy()
    categories = keyword_category()
    # Only skills we actually know how to detect.
    known = [s for s in skills if s in patterns]
    skill_pats = {s: patterns[s] for s in known}
    seniority_re = (
        re.compile(rf"\b{re.escape(seniority)}\b", re.IGNORECASE) if seniority else None
    )
    # A role "matches the profile" if it mentions at least half the listed skills.
    threshold = max(1, (len(skill_pats) + 1) // 2)

    comp_mids: list[int] = []
    matches: list[RoleMatch] = []
    for source, company, title, content, currency, lo, hi in session.execute(
        select(
            AtsJob.source,
            AtsJob.company_name,
            AtsJob.title,
            AtsJob.content_text,
            AtsJob.comp_currency,
            AtsJob.comp_min,
            AtsJob.comp_max,
        ).where(AtsJob.is_open.is_(True), AtsJob.source.in_(_PROFILE_SOURCES))
    ).yield_per(1000):
        text = f"{title or ''} {content or ''}"
        matched = [s for s, p in skill_pats.items() if p.search(text)]
        if not matched:
            continue
        senior_ok = seniority_re is None or bool(seniority_re.search(text))
        mid = (
            (int(lo) + int(hi)) // 2
            if currency == "USD" and lo is not None and hi is not None
            else None
        )
        # Comp benchmark: roles matching the profile (≥1 skill + seniority) that
        # disclose USD pay.
        if senior_ok and mid is not None:
            comp_mids.append(mid)
        # Live openings the profile is a strong match for.
        if len(matched) >= threshold:
            matches.append(
                RoleMatch(
                    company=company,
                    title=title,
                    source=source,
                    skills_matched=len(matched),
                    comp_median_usd=mid,
                )
            )

    p25, med, p75 = (
        _pct(comp_mids, 0.25),
        (int(median(comp_mids)) if comp_mids else 0),
        _pct(comp_mids, 0.75),
    )
    verdict, delta = _comp_verdict(comp_usd, p25, med, p75)

    # Skill demand + trajectory (from the derived keyword trend).
    trend = keyword_trend(session, None)
    by_kw = {k.keyword: k for k in trend.keywords}
    signals = [_signal(s, categories.get(s, "other"), by_kw.get(s)) for s in known]
    # Gaps: rising *skills* (not role modifiers) the profile doesn't already have.
    known_set = set(known)
    gaps = sorted(
        (
            _signal(k.keyword, categories.get(k.keyword, "other"), k)
            for k in trend.keywords
            if k.keyword not in known_set
            and categories.get(k.keyword) in _SKILL_CATEGORIES
            and (k.mom_delta_pts or 0) > 0
        ),
        key=lambda s: s.mom_delta_pts or 0,
        reverse=True,
    )[:3]

    matches.sort(key=lambda r: (r.skills_matched, r.comp_median_usd or 0), reverse=True)
    return MarketFit(
        skills=known,
        seniority=seniority,
        comp_usd=comp_usd,
        comp_n=len(comp_mids),
        comp_p25_usd=p25,
        comp_median_usd=med,
        comp_p75_usd=p75,
        comp_verdict=verdict,
        comp_delta_pct=delta,
        skill_signals=signals,
        gaps=gaps,
        matching_roles=len(matches),
        top_roles=matches[:8],
    )


def _signal(skill: str, category: str, kt: object) -> SkillSignal:
    share = 0.0
    mom = None
    if kt is not None:
        shares = getattr(kt, "shares", None) or []
        latest = next((s for s in reversed(shares) if s is not None), None)
        share = round(latest, 1) if latest is not None else 0.0
        mom = getattr(kt, "mom_delta_pts", None)
    return SkillSignal(
        skill=skill,
        category=category,
        demand_share=share,
        mom_delta_pts=round(mom, 1) if mom is not None else None,
        trajectory=_trajectory(mom),
    )


def _comp_verdict(
    comp_usd: int | None, p25: int, med: int, p75: int
) -> tuple[str, float | None]:
    if comp_usd is None or med == 0:
        return "unknown", None
    delta = round(100.0 * (comp_usd - med) / med, 1)
    if comp_usd < p25:
        return "under", delta
    if comp_usd > p75:
        return "over", delta
    return "fit", delta
