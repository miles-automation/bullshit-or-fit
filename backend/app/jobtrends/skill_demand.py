"""Cross-source skill unification: the HN keyword taxonomy, applied to the live
continuous-board openings (ATS + remote + federal).

`extract_skill_demand` rebuilds `keyword_source_demand` from `ats_jobs` — for every
currently-open role it matches the taxonomy against title + content_text and tallies
per (source, keyword). `skill_demand` pivots that into "share of open roles
mentioning each skill", overall and per source, so demand is finally comparable
across every source (HN's own historical trend already covers its stream).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.jobtrends.extract import compile_taxonomy, match_keywords
from app.jobtrends.models import AtsJob, KeywordSourceDemand
from app.jobtrends.taxonomy import keyword_category

logger = logging.getLogger(__name__)


def extract_skill_demand(session: Session) -> dict[str, int]:
    """Rebuild `keyword_source_demand` from open ats_jobs rows. {sources, rows}."""
    patterns = compile_taxonomy()
    categories = keyword_category()

    matched: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[str, int] = defaultdict(int)

    for source, title, content in session.execute(
        select(AtsJob.source, AtsJob.title, AtsJob.content_text).where(
            AtsJob.is_open.is_(True)
        )
    ).yield_per(1000):
        totals[source] += 1
        text = f"{title or ''} {content or ''}"
        for kw in match_keywords(text, patterns):
            matched[source][kw] += 1

    rows = [
        {
            "source": source,
            "keyword": kw,
            "category": categories[kw],
            "roles_matched": matched[source].get(kw, 0),
            "roles_total": totals[source],
        }
        for source in totals
        for kw in patterns
    ]

    session.execute(delete(KeywordSourceDemand))
    if rows:
        session.execute(insert(KeywordSourceDemand), rows)
    session.commit()
    logger.info(
        "jobtrends: skill demand rebuilt — %s sources, %s rows", len(totals), len(rows)
    )
    return {"sources": len(totals), "rows": len(rows)}


@dataclass(frozen=True)
class SkillDemand:
    keyword: str
    category: str
    roles_matched: int  # across all sources
    share: float  # % of all open roles mentioning it
    by_source: dict[str, float]  # source -> share within that source


@dataclass(frozen=True)
class SkillReport:
    total_roles: int
    sources: list[str]
    skills: list[SkillDemand]


def skill_demand(session: Session, limit: int = 18) -> SkillReport:
    """Top skills by share of live open roles, overall + per source."""
    rows = session.execute(
        select(
            KeywordSourceDemand.source,
            KeywordSourceDemand.keyword,
            KeywordSourceDemand.category,
            KeywordSourceDemand.roles_matched,
            KeywordSourceDemand.roles_total,
        )
    ).all()

    matched: dict[str, int] = defaultdict(int)
    category: dict[str, str] = {}
    per_source: dict[str, dict[str, float]] = defaultdict(dict)
    source_totals: dict[str, int] = {}
    for source, keyword, cat, m, total in rows:
        matched[keyword] += m
        category[keyword] = cat
        source_totals[source] = total
        if total:
            per_source[keyword][source] = round(100.0 * m / total, 1)

    grand_total = sum(source_totals.values())
    skills = [
        SkillDemand(
            keyword=kw,
            category=category[kw],
            roles_matched=matched[kw],
            share=round(100.0 * matched[kw] / grand_total, 1) if grand_total else 0.0,
            by_source=dict(sorted(per_source[kw].items())),
        )
        for kw in matched
    ]
    skills.sort(key=lambda s: s.roles_matched, reverse=True)
    return SkillReport(
        total_roles=grand_total,
        sources=sorted(source_totals),
        skills=skills[:limit],
    )


def format_skill_table(report: SkillReport) -> str:
    if not report.total_roles:
        return "no data — run `extract` after an ATS/remote/USAJobs snapshot"
    lines = [
        f"top skills across {report.total_roles} live open roles "
        f"({', '.join(report.sources)}):",
        "",
        f"{'skill':<16}{'overall':>9}   by source",
        "-" * 52,
    ]
    for s in report.skills:
        by = "  ".join(f"{src}:{pct:.0f}%" for src, pct in s.by_source.items())
        lines.append(f"{s.keyword:<16}{s.share:>8.1f}%   {by}")
    return "\n".join(lines)
