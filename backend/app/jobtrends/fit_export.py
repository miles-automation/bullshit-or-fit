from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobtrends.fit import (
    CLEARANCE_SPONSORED,
    FIT_SOURCES,
    MAX_POSTING_AGE_DAYS,
    MIN_COMP_MAX,
    SMALL_TEAM,
    FitList,
    FitRole,
    company_id,
    fit_roles,
    is_manager_title,
)
from app.jobtrends.models import SourceHealth

EXPORT_SCHEMA = "bof.fit-export/v1"
RANKING = ["small company first", "on company site", "newest", "highest pay"]
POSTING_TEXT_LIMIT = 6000


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat() if moment else None


def fit_reasons(role: FitRole, now: datetime) -> list[str]:
    kind = "engineering manager" if is_manager_title(role.title) else "engineering"
    reasons = [
        f"title is an {kind} role",
        f"location passes the remote-US or commute filter: {role.location or 'not listed'}",
        "no blocking language requirement found",
        "no active clearance required",
    ]
    if role.comp_max is not None:
        reasons.append(
            f"pay tops out at ${role.comp_max // 1000}k (floor ${MIN_COMP_MAX // 1000}k)"
        )
    if role.team_size is not None and role.team_size <= SMALL_TEAM:
        reasons.append(f"small company ({role.team_size} people)")
    days = max(0, (now - role.listed_at).days)
    label = "posted" if role.posted_at is not None else "first seen"
    reasons.append(f"{label} {days} day(s) ago")
    return reasons


def fit_gaps(role: FitRole, active: bool) -> list[str]:
    gaps: list[str] = []
    if role.comp_max is None:
        gaps.append("pay not listed")
    if role.posted_at is None:
        gaps.append("posting date unknown, using first seen")
    if not role.traceable:
        gaps.append("not found on the company's own site")
    if role.official_url is None:
        gaps.append("official posting URL unknown")
    if role.clearance == CLEARANCE_SPONSORED:
        gaps.append("clearance: sponsored or obtainable after hire")
    if role.team_size is None:
        gaps.append("company size unknown")
    if active:
        gaps.append("active application at this company")
    return gaps


def build_export(
    result: FitList,
    *,
    days: int,
    max_age_days: int,
    freshness: list[dict[str, Any]],
    now: datetime,
    text_top: int = 10,
) -> dict[str, Any]:
    roles: list[dict[str, Any]] = []
    for rank, role in enumerate(result.roles, start=1):
        cid = company_id(role.company_name)
        active = cid in result.active_companies
        text = role.posting_text if rank <= text_top else None
        roles.append(
            {
                "rank": rank,
                "role_id": role.role_id,
                "company_id": cid,
                "company_name": role.company_name,
                "title": role.title,
                "location": role.location,
                "source": role.source,
                "listing_url": role.url,
                "official_url": role.official_url,
                "posted_at": _iso(role.posted_at),
                "first_seen": _iso(role.first_seen),
                "pay": {"min": role.comp_min, "max": role.comp_max},
                "team_size": role.team_size,
                "clearance": role.clearance,
                "manager": is_manager_title(role.title),
                "traceable": role.traceable,
                "active_application_at_company": active,
                "fit_reasons": fit_reasons(role, now),
                "gaps": fit_gaps(role, active),
                "posting_text": text[:POSTING_TEXT_LIMIT] if text else None,
            }
        )
    return {
        "schema": EXPORT_SCHEMA,
        "generated_at": _iso(now),
        "window": {"days": days, "max_age_days": max_age_days},
        "filters": {
            "min_pay_max": MIN_COMP_MAX,
            "small_team": SMALL_TEAM,
            "sources": list(FIT_SOURCES),
            "ranking": RANKING,
        },
        "data_freshness": freshness,
        "hidden_tracker_roles": result.hidden,
        "active_application_companies": sorted(result.active_companies),
        "roles": roles,
    }


def source_freshness(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(select(SourceHealth).order_by(SourceHealth.source)).scalars()
    return [
        {
            "source": r.source,
            "state": r.state,
            "last_seen_at": _iso(r.last_seen_at),
            "checked_at": _iso(r.checked_at),
        }
        for r in rows
    ]


def export_fit(
    session: Session,
    *,
    days: int = 1,
    max_age_days: int = MAX_POSTING_AGE_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(tz=UTC)
    result = fit_roles(session, days=days, max_age_days=max_age_days, now=moment)
    return build_export(
        result,
        days=days,
        max_age_days=max_age_days,
        freshness=source_freshness(session),
        now=moment,
    )
