"""jobtrends extract stage: keyword presence over the raw corpus.

Fast, local, and re-runnable — it reads the raw posts and rebuilds the derived
`keyword_month_stats` table wholesale, so the result always reflects the CURRENT
taxonomy over the CURRENT raw corpus. Change taxonomy.py and re-run; no re-fetch.

`match_keywords` is a pure function (text + compiled patterns -> set of hits),
unit-testable without a DB or network.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.jobtrends.models import STREAM_HIRING, HnHiringPost, KeywordMonthStat
from app.jobtrends.taxonomy import flat_keywords, keyword_category

logger = logging.getLogger(__name__)


def compile_taxonomy() -> dict[str, re.Pattern[str]]:
    """{canonical keyword: compiled case-insensitive alias pattern}."""
    return {
        kw: re.compile("|".join(aliases), re.IGNORECASE)
        for kw, aliases in flat_keywords().items()
    }


def match_keywords(text: str, patterns: dict[str, re.Pattern[str]]) -> set[str]:
    """Keywords present in `text` (presence, not frequency)."""
    return {kw for kw, pat in patterns.items() if pat.search(text)}


def extract_all(session: Session) -> dict[str, int]:
    """Rebuild `keyword_month_stats` from every raw post. Returns a small summary.

    Wholesale rebuild inside one transaction: the derived table is never a source
    of truth, so replacing it outright is the simplest way to stay exactly in sync
    with the taxonomy. Cheap at this scale (thousands of posts x tens of keywords).
    """
    patterns = compile_taxonomy()
    categories = keyword_category()

    matched: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[date, int] = defaultdict(int)

    # Stream raw HIRING posts (month, text) so we never hold the whole corpus in
    # memory. Keyword trends are about job demand, so the candidate stream is excluded.
    for month, raw_text in session.execute(
        select(HnHiringPost.month, HnHiringPost.raw_text).where(
            HnHiringPost.stream == STREAM_HIRING
        )
    ).yield_per(1000):
        totals[month] += 1
        for kw in match_keywords(raw_text, patterns):
            matched[month][kw] += 1

    rows = [
        {
            "month": month,
            "keyword": kw,
            "category": categories[kw],
            "posts_matched": matched[month].get(kw, 0),
            "posts_total": totals[month],
        }
        for month in totals
        for kw in patterns
    ]

    # Replace the derived table wholesale.
    session.execute(delete(KeywordMonthStat))
    if rows:
        session.execute(insert(KeywordMonthStat), rows)
    session.commit()

    logger.info(
        "jobtrends: extracted %s keywords across %s months (%s stat rows)",
        len(patterns),
        len(totals),
        len(rows),
    )
    return {"keywords": len(patterns), "months": len(totals), "rows": len(rows)}


def rebuild_derived(session: Session) -> None:
    """Rebuild every derived table from the raw corpus: keyword stats, comp,
    cohorts, and per-stream volume.

    Imported lazily to keep this module's import graph flat. All are cheap and
    fully reconstructable from raw.
    """
    from app.jobtrends.comp import extract_comp
    from app.jobtrends.market import extract_streams
    from app.jobtrends.recurrence import extract_cohorts
    from app.jobtrends.skill_demand import extract_skill_demand

    extract_all(session)
    extract_comp(session)
    extract_cohorts(session)
    extract_streams(session)
    extract_skill_demand(session)
