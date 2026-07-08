"""OEWS location wage bands — the long-tail, location-real comp signal.

The live-role feeds (HN/ATS/remote) are the HEAD of the market: famous companies,
coastal, remote, inflated. For "what does this actually pay where I live," the
ground truth is BLS OEWS — wage percentiles per state, all employers. bls.gov
bot-blocks scrapers and its public API doesn't serve OEWS wages, so we source the
same figures via O*NET (which serves cleanly and embeds them as chart JSON).

`parse_onet_annual` is pure; OewsClient is httpx with an injectable transport.
The data is annual, so `load_oews` refreshes occasionally, not every tick.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.jobtrends.models import OewsWage

logger = logging.getLogger(__name__)

# The occupation the seeker tool cares about. Extensible to more SOCs later.
SOFTWARE_DEVELOPERS = ("15-1252", "Software Developers")

# 50 states + DC (O*NET uses 2-letter `st=` codes).
STATES: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0 Safari/537.36"


def parse_onet_annual(html: str) -> dict[str, dict[int, int]]:
    """O*NET local-wages page -> {'national': {pct: usd}, 'state': {pct: usd}}.

    The page embeds the figures in a `createWagesChart({...})` call; we read the
    annual one (there's a separate hourly chart)."""
    ann = None
    for call in re.findall(r"createWagesChart\((\{.*?\})\);", html, re.S):
        if "annualChartContainer" in call:
            ann = call
            break
    if ann is None:
        return {}
    out: dict[str, dict[int, int]] = {}
    for key, scope in (("dataNational", "national"), ("dataState", "state")):
        m = re.search(rf'"{key}":(\[[^\]]*\])', ann)
        if not m:
            continue
        bands: dict[int, int] = {}
        for obj in re.findall(r"\{[^}]*\}", m.group(1)):
            pm = re.search(r'"percent":"(\d+)"', obj)
            xm = re.search(r'"x":"?(\d+)"?', obj)
            if pm and xm:
                bands[int(pm.group(1))] = int(xm.group(1))
        if bands:
            out[scope] = bands
    return out


class OewsClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def fetch(self, soc: str, state: str) -> str:
        url = f"https://www.onetonline.org/link/localwages/{soc}.00"
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self._transport,
            follow_redirects=True,
        ) as client:
            resp = client.get(url, params={"st": state}, headers={"User-Agent": _UA})
            resp.raise_for_status()
            return resp.text


def _row(
    soc: str, occ: str, atype: str, code: str, name: str, b: dict[int, int]
) -> dict[str, Any]:
    return {
        "soc": soc,
        "occupation": occ,
        "area_type": atype,
        "area_code": code,
        "area_name": name,
        "p10_usd": b[10],
        "p25_usd": b[25],
        "median_usd": b[50],
        "p75_usd": b[75],
        "p90_usd": b[90],
    }


def _upsert(session: Session, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = pg_insert(OewsWage).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[OewsWage.soc, OewsWage.area_code],
        set_={
            "occupation": stmt.excluded.occupation,
            "area_type": stmt.excluded.area_type,
            "area_name": stmt.excluded.area_name,
            "p10_usd": stmt.excluded.p10_usd,
            "p25_usd": stmt.excluded.p25_usd,
            "median_usd": stmt.excluded.median_usd,
            "p75_usd": stmt.excluded.p75_usd,
            "p90_usd": stmt.excluded.p90_usd,
            "updated_at": func.now(),
        },
    )
    session.execute(stmt)


def load_oews(
    session: Session,
    client: OewsClient,
    occupation: tuple[str, str] = SOFTWARE_DEVELOPERS,
) -> dict[str, int]:
    """Fetch per-state OEWS wage bands (+ national, embedded in every page) and
    upsert. Idempotent; one bad state is logged and skipped. {states}."""
    soc, occ = occupation
    ok = 0
    got_national = False
    for code, name in STATES.items():
        try:
            data = parse_onet_annual(client.fetch(soc, code))
        except Exception:  # noqa: BLE001 — one state must not sink the load
            logger.exception("jobtrends: OEWS fetch failed for %s", code)
            continue
        rows = []
        if not got_national and _complete(data.get("national")):
            rows.append(
                _row(soc, occ, "national", "US", "United States", data["national"])
            )
            got_national = True
        if _complete(data.get("state")):
            rows.append(_row(soc, occ, "state", code, name, data["state"]))
        if rows:
            _upsert(session, rows)
            session.commit()
            ok += 1
    logger.info("jobtrends: OEWS loaded — %s/%s states (%s)", ok, len(STATES), occ)
    return {"states": ok}


def _complete(bands: dict[int, int] | None) -> bool:
    if not bands:
        return False
    return all(p in bands for p in (10, 25, 50, 75, 90))


@dataclass(frozen=True)
class WageBand:
    area_code: str
    area_name: str
    p10_usd: int
    p25_usd: int
    median_usd: int
    p75_usd: int
    p90_usd: int


@dataclass(frozen=True)
class WageReport:
    occupation: str
    area: WageBand | None
    national: WageBand | None


def _band(row: OewsWage) -> WageBand:
    return WageBand(
        area_code=row.area_code,
        area_name=row.area_name,
        p10_usd=row.p10_usd,
        p25_usd=row.p25_usd,
        median_usd=row.median_usd,
        p75_usd=row.p75_usd,
        p90_usd=row.p90_usd,
    )


def wage_bands(
    session: Session, area_code: str, soc: str = SOFTWARE_DEVELOPERS[0]
) -> WageReport:
    """A state's wage band + national, for the given occupation."""
    rows = {
        r.area_code: r
        for r in session.execute(
            select(OewsWage).where(
                OewsWage.soc == soc,
                OewsWage.area_code.in_([area_code.upper(), "US"]),
            )
        ).scalars()
    }
    occ = next((r.occupation for r in rows.values()), SOFTWARE_DEVELOPERS[1])
    area = rows.get(area_code.upper())
    nat = rows.get("US")
    return WageReport(
        occupation=occ,
        area=_band(area) if area else None,
        national=_band(nat) if nat else None,
    )


def available_areas(
    session: Session, soc: str = SOFTWARE_DEVELOPERS[0]
) -> list[dict[str, str]]:
    """Loaded state areas (for the location picker)."""
    rows = session.execute(
        select(OewsWage.area_code, OewsWage.area_name)
        .where(OewsWage.soc == soc, OewsWage.area_type == "state")
        .order_by(OewsWage.area_name)
    ).all()
    return [{"code": c, "name": n} for c, n in rows]
