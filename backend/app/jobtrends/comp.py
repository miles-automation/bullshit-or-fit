"""jobtrends comp stage: parse salary ranges from raw post text.

`parse_comp` is a pure, heuristic salary extractor tuned for HN "Who is hiring?"
posts. It deliberately favors PRECISION over recall: it rejects the numbers that
look salary-ish but aren't — funding ("$42M Series B"), GitHub stars ("25k
stars"), ARR/valuation, user counts — which the naive Phase-1 regex swept in.

Scope (v1): annual salary figures in k-form ("$150k–195k") and comma-form
("$145,000-$170,000"), currencies $/€/£, plus $/hr hourly. Equity-only and
"competitive" get no row. `extract_comp` rebuilds the derived `post_comp` table
from raw; `comp_trend` aggregates it per month.
"""

from __future__ import annotations

import html
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.jobtrends.models import (
    STREAM_HIRING,
    AtsJob,
    CompSourceStat,
    HnHiringPost,
    PostComp,
)

logger = logging.getLogger(__name__)

_CUR = {"$": "USD", "€": "EUR", "£": "GBP"}

# Non-salary context near a number → reject. HN posts pack funding, traction, and
# team-size numbers next to job details, so proximity is the cheapest strong signal.
_NEG = re.compile(
    r"\b(series\s?[a-e]\b|raised|funding|valuation|\barr\b|\bmrr\b|revenue|"
    r"stars?|\busers?\b|customers?|downloads?|employees|team of|backed|"
    r"round|\bytd\b|profit|\bgmv\b)",
    re.IGNORECASE,
)

_SEP = r"(?:\s*(?:[-–—]|to|/)\s*)"  # range separator: - – — "to" /
_NUM = r"\d{1,3}(?:\.\d)?"

# k-form: 150k, $150k–195k, 180-200K
_RANGE_K = re.compile(rf"([$€£])?\s?({_NUM})\s?[kK]?{_SEP}([$€£])?\s?({_NUM})\s?[kK]")
_SINGLE_K = re.compile(rf"([$€£])?\s?({_NUM})\s?[kK]\b")
# comma-form: $150,000 – $195,000
_RANGE_C = re.compile(
    rf"([$€£])?\s?({_NUM}),(\d{{3}}){_SEP}([$€£])?\s?({_NUM}),(\d{{3}})"
)
_SINGLE_C = re.compile(rf"([$€£])\s?({_NUM}),(\d{{3}})\b")
# hourly: $75/hr, $60-90/hour
_HOURLY = re.compile(
    rf"([$€£])?\s?({_NUM})(?:{_SEP}([$€£])?\s?({_NUM}))?\s?(?:/|\bper\s)\s?(?:hr|hour)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CompRange:
    currency: str
    min_amount: int  # in currency units, annualized
    max_amount: int
    period: str  # 'year' (hourly is annualized ×2080, but flagged)
    raw_match: str

    @property
    def midpoint(self) -> int:
        return (self.min_amount + self.max_amount) // 2


def _cur(*symbols: str | None) -> str:
    for s in symbols:
        if s and s in _CUR:
            return _CUR[s]
    return "USD"


def _negated(text: str, start: int, end: int) -> bool:
    # A non-salary word hugging the number ("25k stars", "$30k MRR", "500k users")
    # is the reliable tell. Keep the window tight and asymmetric so a funding clause
    # elsewhere in the sentence ("Series A startup, comp $150k") doesn't poison a
    # real salary — the noise word almost always sits immediately after the figure.
    window = text[max(0, start - 8) : end + 12]
    return bool(_NEG.search(window))


def _plausible_annual(lo: int, hi: int) -> bool:
    return 30_000 <= lo <= hi <= 900_000


def parse_comp(raw_text: str) -> CompRange | None:
    """First plausible salary in the post, or None. Precision-first."""
    text = html.unescape(raw_text)

    # 1) k-form range, then comma range (strongest, unambiguous).
    for m in _RANGE_K.finditer(text):
        if _negated(text, m.start(), m.end()):
            continue
        lo, hi = int(float(m.group(2)) * 1000), int(float(m.group(4)) * 1000)
        if lo <= hi and _plausible_annual(lo, hi):
            return CompRange(
                _cur(m.group(1), m.group(3)), lo, hi, "year", m.group(0).strip()
            )

    for m in _RANGE_C.finditer(text):
        if _negated(text, m.start(), m.end()):
            continue
        lo, hi = int(m.group(2)) * 1000, int(m.group(5)) * 1000
        if lo <= hi and _plausible_annual(lo, hi):
            return CompRange(
                _cur(m.group(1), m.group(4)), lo, hi, "year", m.group(0).strip()
            )

    # 2) single k / comma value.
    for pat, mul in ((_SINGLE_K, 1000), (_SINGLE_C, 1000)):
        for m in pat.finditer(text):
            if _negated(text, m.start(), m.end()):
                continue
            val = int(float(m.group(2)) * mul)
            if _plausible_annual(val, val):
                return CompRange(_cur(m.group(1)), val, val, "year", m.group(0).strip())

    # 3) hourly → annualize (2080 h/yr) so it lands on the same axis, but flag period.
    for m in _HOURLY.finditer(text):
        if _negated(text, m.start(), m.end()):
            continue
        lo_h = float(m.group(2))
        hi_h = float(m.group(4)) if m.group(4) else lo_h
        if 15 <= lo_h <= hi_h <= 500:
            lo, hi = int(lo_h * 2080), int(hi_h * 2080)
            return CompRange(
                _cur(m.group(1), m.group(3)), lo, hi, "hour", m.group(0).strip()
            )

    return None


def extract_comp(session: Session) -> dict[str, int]:
    """Rebuild `post_comp` from raw. Returns {posts, with_comp}."""
    rows: list[dict[str, object]] = []
    total = 0
    for hn_id, month, raw_text in session.execute(
        select(HnHiringPost.hn_id, HnHiringPost.month, HnHiringPost.raw_text).where(
            HnHiringPost.stream == STREAM_HIRING
        )
    ).yield_per(1000):
        total += 1
        comp = parse_comp(raw_text)
        if comp is None:
            continue
        rows.append(
            {
                "hn_id": hn_id,
                "month": month,
                "currency": comp.currency,
                "min_amount": comp.min_amount,
                "max_amount": comp.max_amount,
                "midpoint": comp.midpoint,
                "period": comp.period,
                "raw_match": comp.raw_match[:120],
            }
        )

    session.execute(delete(PostComp))
    if rows:
        session.execute(insert(PostComp), rows)
    session.commit()
    logger.info("jobtrends: comp parsed for %s/%s posts", len(rows), total)
    return {"posts": total, "with_comp": len(rows)}


@dataclass(frozen=True)
class CompMonth:
    month: str  # 'YYYY-MM'
    posts_with_comp: int
    posts_total: int
    coverage_pct: float
    median_midpoint: int
    p25_midpoint: int
    p75_midpoint: int


def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    idx = min(len(s) - 1, int(q * len(s)))
    return s[idx]


def comp_trend(session: Session) -> list[CompMonth]:
    """Per-month comp coverage + midpoint quartiles (USD annual rows only)."""
    totals: dict[date, int] = {
        month: count
        for month, count in session.execute(
            select(HnHiringPost.month, func.count())
            .where(HnHiringPost.stream == STREAM_HIRING)
            .group_by(HnHiringPost.month)
        ).all()
    }
    mids: dict[date, list[int]] = defaultdict(list)
    for month, midpoint in session.execute(
        select(PostComp.month, PostComp.midpoint).where(PostComp.currency == "USD")
    ).all():
        mids[month].append(midpoint)

    out: list[CompMonth] = []
    for month in sorted(totals):
        vals = mids.get(month, [])
        total = totals[month]
        out.append(
            CompMonth(
                month=month.strftime("%Y-%m"),
                posts_with_comp=len(vals),
                posts_total=total,
                coverage_pct=(100.0 * len(vals) / total) if total else 0.0,
                median_midpoint=int(median(vals)) if vals else 0,
                p25_midpoint=_pct(vals, 0.25),
                p75_midpoint=_pct(vals, 0.75),
            )
        )
    return out


def format_comp_table(months: list[CompMonth]) -> str:
    if not months:
        return "no data — run `extract` first"
    lines = [
        f"{'month':<9}{'coverage':>10}{'p25':>10}{'median':>10}{'p75':>10}",
        "-" * 49,
    ]
    for m in months:
        lines.append(
            f"{m.month:<9}{m.posts_with_comp:>4}/{m.posts_total:<3} "
            f"{f'${m.p25_midpoint // 1000}k':>9} {f'${m.median_midpoint // 1000}k':>9} "
            f"{f'${m.p75_midpoint // 1000}k':>9}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Cross-source comp unification                                               #
# --------------------------------------------------------------------------- #
#
# Comp arrives two ways: HN posts are free-text-parsed into `post_comp` (above);
# continuous-board roles carry comp on `ats_jobs`. USAJobs ships a *structured*
# pay field (near-total coverage); Greenhouse/remote roles rarely disclose pay,
# so we fall back to the same free-text heuristic over title+content. Everything
# annualizes onto one USD axis so median pay is finally comparable per channel.

# Annualization multipliers for USAJobs pay intervals. The API variously supplies
# the human string ("Per Year") or a short code ("PA"), so both are keyed here.
# Anything variable ("Without Compensation", piece/fee) is absent → rejected: it
# can't be annualized honestly.
_INTERVAL_MULT: dict[str, int] = {
    "per year": 1,
    "pa": 1,
    "per hour": 2080,
    "ph": 2080,
    "per day": 260,
    "pd": 260,
    "per week": 52,
    "pw": 52,
    "bi-weekly": 26,
    "per bi-weekly": 26,
    "bw": 26,
    "per month": 12,
    "pm": 12,
}
# Structured federal pay legitimately spans low GS grades to senior SES, so the
# plausibility gate is wider than the precision-first free-text floor.
_STRUCT_MIN, _STRUCT_MAX = 10_000, 1_000_000


def annualize_structured(
    min_raw: object, max_raw: object, interval: str | None
) -> tuple[int, int] | None:
    """(min, max) annualized USD from a USAJobs PositionRemuneration entry, or None.

    Amounts arrive as strings; interval is the human 'Description' ('Per Year',
    'Per Hour', …). Returns None for missing/variable/implausible pay.
    """
    mult = _INTERVAL_MULT.get((interval or "per year").strip().lower())
    if mult is None:
        return None
    try:
        lo = int(round(float(str(min_raw))) * mult)
        hi = int(round(float(str(max_raw))) * mult)
    except (TypeError, ValueError):
        return None
    if lo > hi:
        lo, hi = hi, lo
    if lo <= 0 or not (_STRUCT_MIN <= lo <= hi <= _STRUCT_MAX):
        return None
    return lo, hi


def parsed_comp_fields(title: str, content: str) -> dict[str, Any] | None:
    """Free-text comp for a board role (title+content) as ats_jobs column values,
    or None. Reuses the precision-first HN parser; kind='parsed'."""
    comp = parse_comp(f"{title or ''}\n{content or ''}")
    if comp is None:
        return None
    return {
        "comp_min": comp.min_amount,
        "comp_max": comp.max_amount,
        "comp_currency": comp.currency,
        "comp_period": comp.period,
        "comp_kind": "parsed",
    }


@dataclass(frozen=True)
class CompSourceRow:
    source: str
    n_roles: int
    n_with_comp: int
    coverage_pct: float
    p25_usd: int
    median_usd: int
    p75_usd: int


def _quartiles(values: list[int]) -> tuple[int, int, int]:
    if not values:
        return 0, 0, 0
    return _pct(values, 0.25), int(median(values)), _pct(values, 0.75)


def extract_comp_sources(session: Session) -> dict[str, int]:
    """Rebuild `comp_source_stat`: pay quartiles per source on one USD axis.

    Sources: 'hn' (from post_comp) + each continuous-board source ('ats',
    'remote_board', 'usajobs') from ats_jobs comp columns (USD, currently open).
    Wholesale replace — fully reconstructable from raw.
    """
    stats: dict[str, tuple[int, list[int]]] = {}

    # HN: denominator is all hiring posts; comp midpoints are the USD post_comp rows.
    hn_total = session.scalar(
        select(func.count()).where(HnHiringPost.stream == STREAM_HIRING)
    )
    hn_mids = [
        mid
        for (mid,) in session.execute(
            select(PostComp.midpoint).where(PostComp.currency == "USD")
        ).all()
    ]
    stats["hn"] = (int(hn_total or 0), hn_mids)

    # Continuous boards: denominator is currently-open roles; comp is the USD
    # midpoint of the annualized range on each row that has one.
    for source, total in session.execute(
        select(AtsJob.source, func.count())
        .where(AtsJob.is_open.is_(True))
        .group_by(AtsJob.source)
    ).all():
        stats[source] = (int(total), [])
    for source, lo, hi in session.execute(
        select(AtsJob.source, AtsJob.comp_min, AtsJob.comp_max).where(
            AtsJob.is_open.is_(True),
            AtsJob.comp_currency == "USD",
            AtsJob.comp_min.is_not(None),
        )
    ).all():
        stats[source][1].append((int(lo) + int(hi)) // 2)

    rows = []
    for source, (n_roles, mids) in stats.items():
        p25, med, p75 = _quartiles(mids)
        rows.append(
            {
                "source": source,
                "n_roles": n_roles,
                "n_with_comp": len(mids),
                "coverage_pct": (100.0 * len(mids) / n_roles) if n_roles else 0.0,
                "p25_usd": p25,
                "median_usd": med,
                "p75_usd": p75,
            }
        )

    session.execute(delete(CompSourceStat))
    if rows:
        session.execute(insert(CompSourceStat), rows)
    session.commit()
    logger.info("jobtrends: comp sources rebuilt — %s sources", len(rows))
    return {"sources": len(rows)}


def comp_sources(session: Session) -> list[CompSourceRow]:
    """Per-source comp stats, richest median first (sources with data on top)."""
    rows = session.execute(
        select(
            CompSourceStat.source,
            CompSourceStat.n_roles,
            CompSourceStat.n_with_comp,
            CompSourceStat.coverage_pct,
            CompSourceStat.p25_usd,
            CompSourceStat.median_usd,
            CompSourceStat.p75_usd,
        )
    ).all()
    out = [
        CompSourceRow(
            source=source,
            n_roles=n_roles,
            n_with_comp=n_with_comp,
            coverage_pct=round(coverage, 1),
            p25_usd=p25,
            median_usd=med,
            p75_usd=p75,
        )
        for source, n_roles, n_with_comp, coverage, p25, med, p75 in rows
    ]
    out.sort(key=lambda r: (r.n_with_comp > 0, r.median_usd), reverse=True)
    return out


def format_comp_sources(rows: list[CompSourceRow]) -> str:
    if not rows:
        return "no data — run a snapshot + `extract` first"
    lines = [
        f"{'source':<14}{'coverage':>14}{'p25':>9}{'median':>9}{'p75':>9}",
        "-" * 55,
    ]
    for r in rows:
        cov = f"{r.n_with_comp}/{r.n_roles} ({r.coverage_pct:.0f}%)"
        lines.append(
            f"{r.source:<14}{cov:>14}{f'${r.p25_usd // 1000}k':>9}"
            f"{f'${r.median_usd // 1000}k':>9}{f'${r.p75_usd // 1000}k':>9}"
        )
    return "\n".join(lines)
