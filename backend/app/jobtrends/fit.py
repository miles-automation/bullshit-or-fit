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
    r"\b(?:(?:(?:active|current|interim|dod|dow)\s+)?(?:top\s+)?secret\s+(?:security\s+)?clearances?"
    r"|(?:active|current|interim|dod|dow)\s+(?:top\s+)?secret"
    r"|top secret"
    r"|ts/sci(?:\s+(?:security\s+)?clearances?)?"
    r"|public trust(?:\s+clearances?)?"
    r"|(?:security\s+)?clearances?)\b",
    re.I,
)
_CLAUSE = re.compile(
    r"\.(?=\s|$)|[;\n\u2022\u25cf\u25aa]|\s[-\u2013\u2014]\s|,(?!\s*or\b)"
)
_NEG_BEFORE = re.compile(
    r"(\b(no|without)\s+(?:[\w/-]+\s+){0,3}"
    r"|\b(?:do|does|will|did)\s+not\s+(?:need|require|have to have)\s+(?:[\w/-]+\s+){0,3}"
    r"|n't\s+(?:need|require)\s+(?:[\w/-]+\s+){0,3}"
    r"|\bnot\s+(?:required|needed|necessary|expected)\s+to\s+(?:have|possess|hold|maintain)\s+(?:[\w/-]+\s+){0,3})$",
    re.I,
)
_NEG_AFTER = re.compile(
    r"^[^.;]{0,35}?(\bnot (?:required|needed|necessary|a prerequisite)\b|\bnone)", re.I
)
_OBT_BEFORE = re.compile(
    r"(?<!visa )\b(obtain\w*|acquire|be granted|eligib\w*|sponsor\w*|willing\w*)\b[^.;]{0,50}$",
    re.I,
)
_OBT_AFTER = re.compile(
    r"^(\W*or\b[^.;]{0,40}?\b(obtain|eligib\w*|able to|willing\w*|acquire)"
    r"|\W*(?:required|requirement|level(?:\s+must\s+be)?)?\W*(?:the\s+)?(able|ability|eligib\w*)"
    r"\s+to\s+(obtain|acquire|get)"
    r"|\W*(post-start|after start|once hired|after hire)\b"
    r"|[^.;]{0,30}?(?<!visa )\bsponsor\w*)",
    re.I,
)
_STRICT_BEFORE = re.compile(
    r"\b(must|required|requires?|minimum|currently hold|currently possess)\b[^.;]{0,60}$",
    re.I,
)
_STRICT_AFTER = re.compile(
    r"^[^.;]{0,25}?\b(required|requires?|requirement|must|mandatory)\b", re.I
)
_ACTIVE_BEFORE = re.compile(r"\b(active|current|existing)\b[^.;]{0,35}$", re.I)
_ACTIVE_AFTER = re.compile(r"^[^.;]{0,25}?\((?:active|current)\)", re.I)
_SOFT_AFTER = re.compile(
    r"^\s*(?:or\s+[\w/-]+\s+)?(?:\([^)]{0,30}\)\s*)?(?:is|are|would be)?\s*(?:a\s+|strongly\s+|highly\s+)?"
    r"(preferred|desired|a plus|plus|bonus|nice[- ]to[- ]have|optional|advantageous)\b",
    re.I,
)
_SOFT_BEFORE = re.compile(
    r"\b(ideally|preferably|preference (?:for|given to)|nice if)\b[^.;]{0,50}$", re.I
)
_SOFT_LABEL_BEFORE = re.compile(
    r"\b(nice[- ]to[- ]have|preferred|bonus|desired|pluses)\b[^:]{0,30}:[^:]{0,45}$",
    re.I,
)
_BEFORE = 60
_LABEL = re.compile(
    r"^\s*((?:level|requirements?|required|type|status)\b[^.;:\n]{0,30})?:\s*", re.I
)
_LABEL_NONE = re.compile(
    r"^\s*(?:[Nn]one|NONE|N/A|n/a|[Nn]ot [Rr]equired|No|NO)(?![a-z])"
)
_LABEL_OBTAIN = re.compile(r"\b(obtain\w*|eligib\w*|sponsor\w*|acquire)\b", re.I)
_LABEL_HARD = re.compile(
    r"\b(active|current|must|required|requires?|requirement|minimum|possess|hold)\b|\((?:active|current)\)",
    re.I,
)


def _label_status(label: str, rest: str) -> str | None:
    if _LABEL_NONE.search(rest):
        return None
    head = f"{label} {rest[:80]}"
    if _LABEL_OBTAIN.search(head):
        return CLEARANCE_SPONSORED
    if _SOFT_AFTER.search(rest[:40]) or _SOFT_AFTER.search(f" {label}"):
        return CLEARANCE_SPONSORED
    if _LABEL_HARD.search(f"{label} {rest[:40]}"):
        return CLEARANCE_REQUIRED
    return None


def _clause_status(clause: str) -> str | None:
    status: str | None = None
    for m in _CLEARANCE_TERM.finditer(clause):
        before = clause[max(0, m.start() - _BEFORE) : m.start()]
        after = clause[m.end() : m.end() + 60]
        label = _LABEL.match(clause[m.end() :])
        if label:
            labelled = _label_status(
                label.group(1) or "", clause[m.end() + label.end() :]
            )
            if labelled == CLEARANCE_REQUIRED:
                return CLEARANCE_REQUIRED
            status = status or labelled
            continue
        if _NEG_BEFORE.search(before) or _NEG_AFTER.search(after):
            continue
        if _OBT_BEFORE.search(before) or _OBT_AFTER.search(after):
            status = status or CLEARANCE_SPONSORED
            continue
        if (
            _SOFT_AFTER.search(after)
            or _SOFT_LABEL_BEFORE.search(before)
            or _SOFT_BEFORE.search(before)
        ) and not _STRICT_AFTER.search(after):
            status = status or CLEARANCE_SPONSORED
            continue
        term_and_before = before + m.group(0)
        if (
            _STRICT_BEFORE.search(before)
            or _STRICT_AFTER.search(after)
            or _ACTIVE_BEFORE.search(term_and_before)
            or _ACTIVE_AFTER.search(after)
        ):
            return CLEARANCE_REQUIRED
    return status


_SOFT_HEADING = re.compile(
    r"\b(preferred (?:qualifications|skills|experience|requirements)|nice[- ]to[- ]haves?"
    r"|bonus points|pluses|desired (?:skills|qualifications|experience)|what would be nice"
    r"|preferred\s*:)",
    re.I,
)
_HARD_HEADING = re.compile(
    r"\b(required (?:qualifications|skills|experience)|minimum qualifications|basic qualifications"
    r"|requirements\s*:|what you(?:'ll| will) need|must[- ]haves?|who you are|what you bring)",
    re.I,
)
_EXPLICIT_REQUIRED = re.compile(
    r"\b(required|requires?|must|minimum|mandatory)\b", re.I
)


def _clause_spans(body: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _CLAUSE.finditer(body):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(body)))
    return spans


def _soft_scope_marks(body: str) -> list[tuple[int, bool]]:
    marks = [(m.end(), True) for m in _SOFT_HEADING.finditer(body)]
    marks += [(m.end(), False) for m in _HARD_HEADING.finditer(body)]
    return sorted(marks)


def _in_soft_scope(marks: list[tuple[int, bool]], position: int) -> bool:
    current = False
    for at, soft in marks:
        if at > position:
            break
        current = soft
    return current


def clearance_status(text: str | None) -> str | None:
    body = re.sub(r"\bu\.s\.", "US", text or "", flags=re.I)
    marks = _soft_scope_marks(body)
    result: str | None = None
    for start, end in _clause_spans(body):
        clause = body[start:end]
        status = _clause_status(clause)
        if status == CLEARANCE_REQUIRED:
            if _in_soft_scope(marks, end) and not _EXPLICIT_REQUIRED.search(clause):
                status = CLEARANCE_SPONSORED
            else:
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
