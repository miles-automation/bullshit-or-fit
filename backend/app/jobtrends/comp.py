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

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.jobtrends.models import HnHiringPost, PostComp

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
        select(HnHiringPost.hn_id, HnHiringPost.month, HnHiringPost.raw_text)
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
            select(HnHiringPost.month, func.count()).group_by(HnHiringPost.month)
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
