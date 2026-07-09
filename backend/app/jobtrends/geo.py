"""Geography: where the open roles are, and how remote.

The continuous-board `location` field is messy free text ("San Francisco, CA;
Remote, US", "Washington, District of Columbia", "Remote - USA"). `classify_location`
normalizes it to a single metro bucket plus a remote flag; `extract_geo` rebuilds
the derived `location_stat` per source each tick, and `geo_report` ranks metros and
computes remote share.

Deliberately coarse and precision-lenient: it's a directional "top metros" signal,
not a geocoder. A role tagged with a city AND "remote" counts under the city for
the metro ranking and also toward the remote tally.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.jobtrends.ats import PUBLIC_ATS_SOURCES, SOURCE_REMOTE
from app.jobtrends.models import AtsJob, LocationStat

logger = logging.getLogger(__name__)

# Remote signals anywhere in the location string.
_REMOTE_RE = re.compile(
    r"\b(remote|anywhere|world\s?wide|distributed|work\s?from\s?home|wfh|fully[\s-]?remote)\b",
    re.IGNORECASE,
)

# Metro buckets, checked in order — first alias hit wins. Aliases are matched
# case-insensitively with word boundaries. Ordered so a more specific metro
# (Seattle) is tested before a broader token that could otherwise capture it.
_METROS: list[tuple[str, re.Pattern[str]]] = [
    (canon, re.compile(r"\b(?:" + "|".join(aliases) + r")\b", re.IGNORECASE))
    for canon, aliases in [
        (
            "SF Bay Area",
            [
                "san francisco",
                "bay area",
                "palo alto",
                "mountain view",
                "san jose",
                "sunnyvale",
                "oakland",
                "menlo park",
                "cupertino",
                "santa clara",
                "redwood city",
            ],
        ),
        ("New York", ["new york", "nyc", "brooklyn", "manhattan"]),
        ("Seattle", ["seattle", "bellevue", "redmond"]),
        (
            "Boston",
            ["boston", "cambridge, ma", "cambridge, massachusetts", "somerville"],
        ),
        ("Austin", ["austin"]),
        ("Denver", ["denver", "boulder"]),
        ("Los Angeles", ["los angeles", "santa monica", "pasadena"]),
        ("Chicago", ["chicago"]),
        (
            "Washington DC",
            [
                "district of columbia",
                # Washington DC / D.C. — comma optional, periods optional. Must be a
                # complete token so "Washington, DC" isn't clipped to "…, d".
                r"washington,? d\.?c\.?",
                # Northern-VA duty stations — the state token is required so
                # "Arlington, Texas" doesn't land here.
                r"arlington,? (?:va|virginia)",
                r"alexandria,? (?:va|virginia)",
                "quantico",
                "reston",
            ],
        ),
        ("Atlanta", ["atlanta"]),
        ("Miami", ["miami"]),
        ("Dallas", ["dallas", "fort worth"]),
        ("Toronto", ["toronto"]),
        ("Vancouver", ["vancouver"]),
        ("London", ["london"]),
        ("Berlin", ["berlin"]),
        ("Amsterdam", ["amsterdam"]),
        ("Dublin", ["dublin"]),
        ("Paris", ["paris"]),
        ("Singapore", ["singapore"]),
        ("Bangalore", ["bangalore", "bengaluru"]),
        ("Tel Aviv", ["tel aviv"]),
    ]
]

_VARIOUS_RE = re.compile(
    r"\b(multiple locations?|various|negotiable|nationwide|several)\b", re.IGNORECASE
)


def classify_location(text: str | None) -> tuple[str, bool]:
    """(bucket, is_remote). Bucket is a metro, else 'Remote'/'Various'/'Other'."""
    if not text:
        return "Other", False
    is_remote = bool(_REMOTE_RE.search(text))
    for canon, pat in _METROS:
        if pat.search(text):
            return canon, is_remote
    if _VARIOUS_RE.search(text):
        return "Various", is_remote
    if is_remote:
        return "Remote", True
    return "Other", is_remote


# Non-metro buckets excluded from the "top metros" ranking (they're not places).
_NON_METRO = {"Remote", "Various", "Other"}


def extract_geo(session: Session) -> dict[str, int]:
    """Rebuild `location_stat` from open ats_jobs. {sources, buckets}."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    remote: dict[tuple[str, str], int] = defaultdict(int)

    for source, location in session.execute(
        select(AtsJob.source, AtsJob.location).where(
            AtsJob.is_open.is_(True), AtsJob.source.in_(PUBLIC_ATS_SOURCES)
        )
    ).yield_per(1000):
        # Remote-board feeds are remote by definition — their location field is the
        # candidate's *eligible region*, not an office — so parsing it for a remote
        # flag understates them. Treat the whole source as remote.
        if source == SOURCE_REMOTE:
            bucket, is_remote = "Remote", True
        else:
            bucket, is_remote = classify_location(location)
        counts[(source, bucket)] += 1
        if is_remote:
            remote[(source, bucket)] += 1

    rows = [
        {
            "source": source,
            "bucket": bucket,
            "n_roles": n,
            "remote_roles": remote.get((source, bucket), 0),
        }
        for (source, bucket), n in counts.items()
    ]

    session.execute(delete(LocationStat))
    if rows:
        session.execute(insert(LocationStat), rows)
    session.commit()
    sources = {r["source"] for r in rows}
    logger.info(
        "jobtrends: geo rebuilt — %s buckets across %s sources", len(rows), len(sources)
    )
    return {"sources": len(sources), "buckets": len(rows)}


@dataclass(frozen=True)
class MetroCount:
    metro: str
    n_roles: int


@dataclass(frozen=True)
class GeoSource:
    source: str
    total: int
    remote_pct: float
    top_metros: list[MetroCount]


def geo_report(session: Session, *, limit: int = 10) -> list[GeoSource]:
    """Per source: remote share + top metros (non-metro buckets excluded)."""
    rows = session.execute(
        select(
            LocationStat.source,
            LocationStat.bucket,
            LocationStat.n_roles,
            LocationStat.remote_roles,
        )
    ).all()

    total: dict[str, int] = defaultdict(int)
    remote: dict[str, int] = defaultdict(int)
    metros: dict[str, list[MetroCount]] = defaultdict(list)
    for source, bucket, n, rem in rows:
        total[source] += n
        remote[source] += rem
        if bucket not in _NON_METRO:
            metros[source].append(MetroCount(metro=bucket, n_roles=n))

    out = [
        GeoSource(
            source=source,
            total=total[source],
            remote_pct=round(100.0 * remote[source] / total[source], 1)
            if total[source]
            else 0.0,
            top_metros=sorted(metros[source], key=lambda m: m.n_roles, reverse=True)[
                :limit
            ],
        )
        for source in total
    ]
    out.sort(key=lambda g: g.total, reverse=True)
    return out


def format_geo(sources: list[GeoSource]) -> str:
    if not sources:
        return "no data — run a snapshot + `extract` first"
    lines = []
    for g in sources:
        lines.append(f"{g.source} — {g.total} open, {g.remote_pct}% remote")
        for m in g.top_metros[:6]:
            lines.append(f"    {m.metro:<16}{m.n_roles:>6}")
    return "\n".join(lines)
