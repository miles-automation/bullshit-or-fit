from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.jobtrends.ats import SOURCE_ATS, SOURCE_REMOTE
from app.jobtrends.himalayas import SOURCE_HIMALAYAS
from app.jobtrends.models import AtsJob, DiscoveredBoard

FIT_SOURCES = (SOURCE_ATS, SOURCE_REMOTE, SOURCE_HIMALAYAS, "commute_shed", "adzuna")
MIN_COMP_MAX = 150_000
SMALL_TEAM = 100

_ROLE = re.compile(r"\b(engineer|engineering|developer|architect)\b", re.I)
_EXCLUDED_ROLE = re.compile(
    r"\b(intern|internship|junior|jr|new grad|graduate|manager|director|head of|vp"
    r"|recruit\w*|sales|account|support|qa|test|tester|mechanical|electrical|hardware"
    r"|firmware|embedded|ios|android|mobile|network|civil|structural|field|clinical"
    r"|security|leader|customer|solutions|sales engineer)\b",
    re.I,
)
_EXCLUDED_LANGUAGE = re.compile(
    r"(\b(go|golang|rust|java|scala|elixir|php|kotlin|haskell|clojure|ocaml)\b|c\+\+)",
    re.I,
)
_COMMUTE = re.compile(r"\b(fort collins|cheyenne|laramie|loveland|windsor, co)\b", re.I)
_REMOTE = re.compile(r"\b(remote|anywhere|distributed|work from home|wfh)\b", re.I)
_US = re.compile(
    r"\b(us|usa|u\.s\.|united states|americas|north america|worldwide)\b", re.I
)
_NON_US = re.compile(
    r"\b(canada|uk|united kingdom|europe|emea|germany|india|london|toronto|apac|poland"
    r"|spain|france|brazil|latam|mexico|ireland|amsterdam|netherlands|australia"
    r"|singapore|japan|israel|philippines|argentina|colombia|portugal)\b",
    re.I,
)


def title_fits(title: str) -> bool:
    return bool(
        _ROLE.search(title)
        and not _EXCLUDED_ROLE.search(title)
        and not _EXCLUDED_LANGUAGE.search(title)
    )


def location_fits(location: str | None) -> bool:
    text = location or ""
    if _COMMUTE.search(text):
        return True
    if not _REMOTE.search(text):
        return False
    return bool(_US.search(text)) or not _NON_US.search(text)


def comp_fits(comp_max: int | None, currency: str | None) -> bool:
    if comp_max is None:
        return True
    if currency not in (None, "USD"):
        return False
    return comp_max >= MIN_COMP_MAX


@dataclass(frozen=True)
class FitRole:
    source: str
    company_name: str
    title: str
    location: str | None
    url: str | None
    comp_min: int | None
    comp_max: int | None
    team_size: int | None
    first_seen: datetime


def rank_key(role: FitRole) -> tuple[int, int, float]:
    small = 0 if role.team_size is not None and role.team_size <= SMALL_TEAM else 1
    return (small, -(role.comp_max or 0), -role.first_seen.timestamp())


def fit_roles(
    session: Session,
    *,
    days: int = 1,
    now: datetime | None = None,
) -> list[FitRole]:
    since = (now or datetime.now(tz=UTC)) - timedelta(days=days)
    rows = session.execute(
        select(
            AtsJob.source,
            AtsJob.company_name,
            AtsJob.title,
            AtsJob.location,
            AtsJob.url,
            AtsJob.comp_min,
            AtsJob.comp_max,
            AtsJob.comp_currency,
            AtsJob.first_seen,
            DiscoveredBoard.team_size,
        )
        .outerjoin(
            DiscoveredBoard,
            and_(
                AtsJob.source == SOURCE_ATS,
                DiscoveredBoard.found.is_(True),
                DiscoveredBoard.provider == AtsJob.provider,
                DiscoveredBoard.token == AtsJob.company_token,
            ),
        )
        .where(
            AtsJob.is_open.is_(True),
            AtsJob.first_seen >= since,
            AtsJob.source.in_(FIT_SOURCES),
        )
    ).all()
    roles: dict[tuple[str, str, str | None], FitRole] = {}
    for r in rows:
        if not (
            title_fits(r.title)
            and location_fits(r.location)
            and comp_fits(r.comp_max, r.comp_currency)
        ):
            continue
        role = FitRole(
            source=r.source,
            company_name=r.company_name,
            title=r.title,
            location=r.location,
            url=r.url,
            comp_min=r.comp_min,
            comp_max=r.comp_max,
            team_size=r.team_size,
            first_seen=r.first_seen,
        )
        key = (role.company_name.lower(), role.title.lower(), role.location)
        current = roles.get(key)
        if current is None or rank_key(role) < rank_key(current):
            roles[key] = role
    return sorted(roles.values(), key=rank_key)


def _pay(role: FitRole) -> str:
    if role.comp_max is None:
        return "pay not listed"
    if role.comp_min is None or role.comp_min == role.comp_max:
        return f"${role.comp_max // 1000}k"
    return f"${role.comp_min // 1000}k-${role.comp_max // 1000}k"


def format_fit_list(roles: list[FitRole], days: int) -> str:
    if not roles:
        return f"no new fitting roles in the last {days} day(s)"
    lines = [f"{len(roles)} new fitting roles in the last {days} day(s)", ""]
    for r in roles:
        size = f", {r.team_size} people" if r.team_size is not None else ""
        lines.append(
            f"- {r.company_name}{size}: {r.title} ({r.location or 'location not listed'}; {_pay(r)})"
        )
        lines.append(f"  {r.url or 'no link'}  [{r.source}]")
    return "\n".join(lines)
