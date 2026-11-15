from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
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
_US_STATES = (
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|"
    r"delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|"
    r"kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
    r"mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|"
    r"new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|"
    r"pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|"
    r"utah|vermont|virginia|washington|west virginia|wisconsin|wyoming"
)
_US = re.compile(
    r"\bu\.s\.|\b(us|usa|united states|americas|north america|worldwide|"
    + _US_STATES
    + r")\b",
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
    if _US.search(text):
        return True
    return not re.sub(r"[^a-z]+", "", _REMOTE.sub("", text.lower()))


CLEARANCE_REQUIRED = "required"
CLEARANCE_SPONSORED = "sponsored"

_CLEARANCE_TERM = re.compile(
    r"\b(security clearances?|clearances?|ts/sci|top secret|public trust"
    r"|secret clearance|dod secret|dow secret)\b",
    re.I,
)
_CLAUSE = re.compile(r"[.;\n\u2022\u25cf\u25aa]|\s[-\u2013\u2014]\s|,(?!\s*or\b)")
_NEGATION = re.compile(r"\b(no|not|none|without)\b|n't\b", re.I)
_NEEDED = re.compile(
    r"\b(required|requires?|requirement|needed|necessary)\b|\bnone\b", re.I
)
_OBTAINABLE = re.compile(
    r"\b(obtain|obtaining|obtainable|acquire|be granted|sponsor|sponsors|sponsored|sponsorship"
    r"|eligible to|eligibility to|eligible for|post-start|after start|upon hire|once hired"
    r"|after hire)\b",
    re.I,
)
_STRICT = re.compile(
    r"\b(required|requires|require|must|minimum|currently hold|currently possess)\b",
    re.I,
)
_ACTIVE = re.compile(r"\b(active|current|existing)\b", re.I)
_SOFT = re.compile(
    r"nice[- ]to[- ]have|\b(preferred|desired|bonus|a plus|ideally|optional)\b", re.I
)
_WINDOW = 120
_NEAR = 35


def _clause_status(clause: str) -> str | None:
    status: str | None = None
    for m in _CLEARANCE_TERM.finditer(clause):
        window = clause[max(0, m.start() - _WINDOW) : m.end() + _WINDOW]
        near = clause[max(0, m.start() - _NEAR) : m.end() + _NEAR]
        if _NEGATION.search(near) and _NEEDED.search(near):
            if _OBTAINABLE.search(window):
                status = status or CLEARANCE_SPONSORED
            continue
        if _OBTAINABLE.search(window):
            status = status or CLEARANCE_SPONSORED
            continue
        strict = _STRICT.search(near)
        if strict or _ACTIVE.search(near):
            if _SOFT.search(window) and not strict:
                status = status or CLEARANCE_SPONSORED
                continue
            return CLEARANCE_REQUIRED
        if _SOFT.search(window):
            status = status or CLEARANCE_SPONSORED
    return status


def clearance_status(text: str | None) -> str | None:
    body = re.sub(r"\bu\.s\.", "US", text or "", flags=re.I)
    result: str | None = None
    for clause in _CLAUSE.split(body):
        status = _clause_status(clause)
        if status == CLEARANCE_REQUIRED:
            return CLEARANCE_REQUIRED
        result = result or status
    return result


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
    clearance: str | None = None


def rank_key(role: FitRole) -> tuple[int, int, float]:
    small = 0 if role.team_size is not None and role.team_size <= SMALL_TEAM else 1
    return (small, -(role.comp_max or 0), -role.first_seen.timestamp())


def fit_roles(
    session: Session,
    *,
    days: int = 1,
    now: datetime | None = None,
) -> list[FitRole]:
    moment = now or datetime.now(tz=UTC)
    since = moment - timedelta(days=days)
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
            AtsJob.content_text,
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
            or_(AtsJob.expires_at.is_(None), AtsJob.expires_at > moment),
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
        clearance = clearance_status(f"{r.title}\n{r.content_text}")
        if clearance == CLEARANCE_REQUIRED:
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
            clearance=clearance,
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
        tag = f" [clearance: {r.clearance}]" if r.clearance else ""
        lines.append(f"  {r.url or 'no link'}  [{r.source}]{tag}")
    return "\n".join(lines)
