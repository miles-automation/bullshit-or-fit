"""Comp × skill cross-tab: median advertised pay per skill, per source.

The final unification — it joins the two signals we already extract (the keyword
taxonomy and comp) at the individual-role level, so we can finally answer "what
does a *Go* role pay at companies vs federal vs remote vs HN?".

`extract_skill_comp` rebuilds `skill_comp_stat` each tick: for every role that has
BOTH a USD comp figure and a taxonomy hit — over `ats_jobs` (comp columns) and HN
`post_comp` — it files the role's comp midpoint under every skill it mentions,
per source, then keeps only (source, skill) cells with a real sample. `skill_comp`
pivots that into a per-skill, per-source pay comparison.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.jobtrends.extract import compile_taxonomy, match_keywords
from app.jobtrends.models import (
    STREAM_HIRING,
    AtsJob,
    HnHiringPost,
    PostComp,
    SkillCompStat,
)
from app.jobtrends.taxonomy import keyword_category

logger = logging.getLogger(__name__)

# A (source, skill) cell needs at least this many comped roles to be reported —
# below it the median is noise (a handful of postings).
MIN_SAMPLE = 8


def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def extract_skill_comp(session: Session) -> dict[str, int]:
    """Rebuild `skill_comp_stat` from raw comp × taxonomy. {sources, cells}."""
    patterns = compile_taxonomy()
    categories = keyword_category()

    # buckets[(source, keyword)] = [comp midpoints]
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)

    # Continuous boards: each open role with a USD comp figure, matched over
    # title + content.
    for source, title, content, lo, hi in session.execute(
        select(
            AtsJob.source,
            AtsJob.title,
            AtsJob.content_text,
            AtsJob.comp_min,
            AtsJob.comp_max,
        ).where(
            AtsJob.is_open.is_(True),
            AtsJob.comp_currency == "USD",
            AtsJob.comp_min.is_not(None),
        )
    ).yield_per(1000):
        mid = (int(lo) + int(hi)) // 2
        text = f"{title or ''} {content or ''}"
        for kw in match_keywords(text, patterns):
            buckets[(source, kw)].append(mid)

    # HN: each hiring post with a USD comp midpoint, matched over its raw body.
    for raw_text, mid in session.execute(
        select(HnHiringPost.raw_text, PostComp.midpoint)
        .join(PostComp, PostComp.hn_id == HnHiringPost.hn_id)
        .where(HnHiringPost.stream == STREAM_HIRING, PostComp.currency == "USD")
    ).yield_per(1000):
        for kw in match_keywords(raw_text or "", patterns):
            buckets[("hn", kw)].append(int(mid))

    rows = [
        {
            "source": source,
            "keyword": kw,
            "category": categories[kw],
            "n_with_comp": len(mids),
            "p25_usd": _pct(mids, 0.25),
            "median_usd": int(median(mids)),
            "p75_usd": _pct(mids, 0.75),
        }
        for (source, kw), mids in buckets.items()
        if len(mids) >= MIN_SAMPLE and kw in categories
    ]

    session.execute(delete(SkillCompStat))
    if rows:
        session.execute(insert(SkillCompStat), rows)
    session.commit()
    sources = {r["source"] for r in rows}
    logger.info(
        "jobtrends: skill comp rebuilt — %s cells across %s sources",
        len(rows),
        len(sources),
    )
    return {"sources": len(sources), "cells": len(rows)}


@dataclass(frozen=True)
class SkillCompCell:
    source: str
    n_with_comp: int
    p25_usd: int
    median_usd: int
    p75_usd: int


@dataclass(frozen=True)
class SkillCompRow:
    keyword: str
    category: str
    total_n: int
    by_source: dict[str, SkillCompCell]


@dataclass(frozen=True)
class SkillCompReport:
    sources: list[str]
    skills: list[SkillCompRow]


def skill_comp(
    session: Session, *, limit: int = 16, min_sources: int = 2
) -> SkillCompReport:
    """Per-skill pay compared across sources.

    Only skills present in at least `min_sources` sources are returned (a single-
    source median isn't a comparison), ranked by total comped sample.
    """
    rows = session.execute(
        select(
            SkillCompStat.source,
            SkillCompStat.keyword,
            SkillCompStat.category,
            SkillCompStat.n_with_comp,
            SkillCompStat.p25_usd,
            SkillCompStat.median_usd,
            SkillCompStat.p75_usd,
        )
    ).all()

    by_kw: dict[str, dict[str, SkillCompCell]] = defaultdict(dict)
    category: dict[str, str] = {}
    all_sources: set[str] = set()
    for source, kw, cat, n, p25, med, p75 in rows:
        by_kw[kw][source] = SkillCompCell(
            source=source, n_with_comp=n, p25_usd=p25, median_usd=med, p75_usd=p75
        )
        category[kw] = cat
        all_sources.add(source)

    skills = [
        SkillCompRow(
            keyword=kw,
            category=category[kw],
            total_n=sum(c.n_with_comp for c in cells.values()),
            by_source=dict(sorted(cells.items())),
        )
        for kw, cells in by_kw.items()
        if len(cells) >= min_sources
    ]
    skills.sort(key=lambda s: s.total_n, reverse=True)
    return SkillCompReport(sources=sorted(all_sources), skills=skills[:limit])


def format_skill_comp(report: SkillCompReport) -> str:
    if not report.skills:
        return "no data — run a snapshot + `extract` first"
    lines = [
        f"median pay per skill across {', '.join(report.sources)}:",
        "",
    ]
    for s in report.skills:
        cells = "  ".join(
            f"{src}:${c.median_usd // 1000}k(n={c.n_with_comp})"
            for src, c in s.by_source.items()
        )
        lines.append(f"{s.keyword:<14}{cells}")
    return "\n".join(lines)
