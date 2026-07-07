"""Company pay leaderboard: which companies advertise the highest pay.

A live reader over the currently-open company (source='ats') roles that carry a
USD comp figure — structured (Ashby/Lever) or free-text-parsed (Greenhouse). Reads
`ats_jobs` directly like `ats_report`/`geo_report` (continuous-board current state,
not a derived HN table). Only companies with a real comped sample are ranked, so a
single lucky posting can't top the board.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobtrends.ats import SOURCE_ATS
from app.jobtrends.models import AtsJob

logger = logging.getLogger(__name__)

# A company needs at least this many comped open roles to be ranked.
MIN_SAMPLE = 6


def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


@dataclass(frozen=True)
class CompanyPay:
    company_name: str
    company_token: str
    n_with_comp: int
    p25_usd: int
    median_usd: int
    p75_usd: int


def company_pay(
    session: Session, *, limit: int = 15, min_sample: int = MIN_SAMPLE
) -> list[CompanyPay]:
    """Companies ranked by median advertised pay across their open USD-comp roles."""
    mids: dict[tuple[str, str], list[int]] = defaultdict(list)
    for name, token, lo, hi in session.execute(
        select(
            AtsJob.company_name, AtsJob.company_token, AtsJob.comp_min, AtsJob.comp_max
        ).where(
            AtsJob.is_open.is_(True),
            AtsJob.source == SOURCE_ATS,
            AtsJob.comp_currency == "USD",
            AtsJob.comp_min.is_not(None),
        )
    ).yield_per(1000):
        mids[(name, token)].append((int(lo) + int(hi)) // 2)

    rows = [
        CompanyPay(
            company_name=name,
            company_token=token,
            n_with_comp=len(vals),
            p25_usd=_pct(vals, 0.25),
            median_usd=int(median(vals)),
            p75_usd=_pct(vals, 0.75),
        )
        for (name, token), vals in mids.items()
        if len(vals) >= min_sample
    ]
    rows.sort(key=lambda c: c.median_usd, reverse=True)
    return rows[:limit]


def format_company_pay(rows: list[CompanyPay]) -> str:
    if not rows:
        return "no data — run a snapshot + `extract` first"
    lines = [f"{'company':<18}{'median':>9}{'p25–p75':>16}{'n':>6}", "-" * 49]
    for c in rows:
        band = f"${c.p25_usd // 1000}k–${c.p75_usd // 1000}k"
        lines.append(
            f"{c.company_name:<18}{f'${c.median_usd // 1000}k':>9}{band:>16}"
            f"{c.n_with_comp:>6}"
        )
    return "\n".join(lines)
