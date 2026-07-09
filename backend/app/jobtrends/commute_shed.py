"""Commute-shed radar: the employer map reachable from a home base (Laramie, WY).

This is the private, place-first counterpart to the national `/trends` dashboard.
Where the public product asks "what's the market doing", this answers a personal
question: *who's around me, and what's coming down the pipe*, so that when it's
time to reach out you already know the terrain.

Two layers:

1. The **registry** (`SEED_EMPLOYERS` -> `commute_shed_employer` table): a curated
   map of every reachable employer, tiered by reachability. Complete by hand — it
   carries a `careers_url` and `notes` even for employers we can't machine-read
   (Workday/Taleo shops, tiny startups), so the map is never blank.

2. The **live feed** (a subset): employers with a public Greenhouse/Lever/Ashby
   board get snapshotted into `ats_jobs` tagged `source='commute_shed'`, *filtered
   to roles actually in reach* (their local office or remote) so a big employer's
   global req list can't flood the radar. Open counts join back to the registry.

The report groups the registry by tier, attaches live open counts (+ a 7-day
"just posted" trajectory seed) where we have a feed, and returns the concrete open
local roles. Trajectory deepens naturally as `ats_jobs.first_seen/last_seen`
history accrues from first deploy forward.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.jobtrends.ats import (
    AtsClient,
    Company,
    close_missing,
    upsert_jobs,
)
from app.jobtrends.geo import classify_location
from app.jobtrends.models import AtsJob, CommuteShedEmployer, CommuteShedStat
from app.jobtrends.workday import PROVIDER_WORKDAY, WorkdayClient, WorkdayConfig

logger = logging.getLogger(__name__)

# `source` value stamped on live commute-shed rows in ats_jobs, keeping them
# cleanly separable from the national 'ats' set and the remote aggregators.
SOURCE_COMMUTE_SHED = "commute_shed"

# Reachability tiers (ordered near -> far), each with a human label.
TIER_LARAMIE = "laramie"
TIER_CHEYENNE = "cheyenne"
TIER_FRONT_RANGE = "front_range"
TIER_WY_REMOTE = "wy_remote"

TIER_ORDER = [TIER_LARAMIE, TIER_CHEYENNE, TIER_FRONT_RANGE, TIER_WY_REMOTE]
TIER_LABEL: dict[str, str] = {
    TIER_LARAMIE: "Laramie (in town)",
    TIER_CHEYENNE: "Cheyenne (~50 mi)",
    TIER_FRONT_RANGE: "Front Range, CO (~1–1.5 hr)",
    TIER_WY_REMOTE: "Wyoming-remote",
}

# Location tokens that count as "in reach" for a physical-office tier. A role at a
# big employer only lands on the radar if its location matches the tier area OR the
# role is remote; WY-remote employers are small + aligned, so all their roles pass.
_TIER_AREA_TOKENS: dict[str, tuple[str, ...]] = {
    TIER_LARAMIE: ("laramie",),
    TIER_CHEYENNE: ("cheyenne", "warren", "f.e. warren", "fe warren"),
    TIER_FRONT_RANGE: (
        "fort collins",
        "loveland",
        "berthoud",
        "windsor",
        "greeley",
        "longmont",
        "boulder",
        "colorado",  # a CO-wide posting is plausibly commutable/hybrid; keep it
    ),
}


@dataclass(frozen=True)
class ShedEmployer:
    """One curated commute-shed employer (the seed row)."""

    token: str
    name: str
    tier: str
    category: str
    careers_url: str
    hq_city: str | None = None
    hq_state: str | None = None
    distance_mi: int | None = None
    provider: str | None = None  # greenhouse | lever | ashby | workday, if a feed
    ats_token: str | None = None
    # Workday employers set provider='workday' + this config; ats_token is reused as
    # the row/join token (the CXS API needs tenant/host/site, not a board slug).
    workday: WorkdayConfig | None = None
    # Big national employers: keep only their local-office roles (drop national-remote
    # ones, which belong on /you, not this place-based radar).
    local_only: bool = False
    engineer_relevant: bool = True
    notes: str | None = None


# --- The curated map -------------------------------------------------------
# Real employers reachable from Laramie, WY. Live feeds verified 2026-07-09:
# Ursa Major + Anduril/ex-Numerica (Greenhouse), JumpCloud (Lever), and Broadcom +
# HPE (Workday CXS, Fort-Collins-filtered). The rest are Taleo/iCIMS/private, so
# they're map-only (careers_url + notes) until a feed is found. Watch for
# acquisitions (Teton→Markforged, Numerica→Anduril) staling careers URLs. Add rows
# freely — sync picks them up; a token dropped here is auto-deactivated.
SEED_EMPLOYERS: list[ShedEmployer] = [
    # --- Laramie (in town) ---
    ShedEmployer(
        token="university-of-wyoming",
        name="University of Wyoming",
        tier=TIER_LARAMIE,
        category="university",
        hq_city="Laramie",
        hq_state="WY",
        distance_mi=0,
        careers_url="https://www.uwyo.edu/hr/prospective/",
        notes=(
            "The anchor employer in town. School of Computing + ARCC research "
            "computing; research-software-engineer / staff roles post on "
            "HigherEdJobs. Steady, benefits, in-person."
        ),
    ),
    ShedEmployer(
        token="markforged-laramie",
        name="Markforged Simulation (ex-Teton)",
        tier=TIER_LARAMIE,
        category="startup",
        hq_city="Laramie",
        hq_state="WY",
        distance_mi=0,
        careers_url="https://markforged.com/about/careers",
        notes=(
            "The former Teton Simulation (SmartSlice; UW IMPACT 307 spinout), "
            "acquired by Markforged in 2022 — the Laramie team stayed on in the "
            "incubator. ⚠ Verify still active: Markforged was taken private by Nano "
            "Dimension in 2025 and is small; the local team may have thinned."
        ),
    ),
    ShedEmployer(
        token="trihydro",
        name="Trihydro Corporation",
        tier=TIER_LARAMIE,
        category="engineering",
        hq_city="Laramie",
        hq_state="WY",
        distance_mi=0,
        careers_url="https://www.trihydro.com/careers",
        notes=(
            "Large Laramie-HQ environmental/engineering firm (~700 staff). "
            "In-house software/GIS/data roles; the biggest private tech-adjacent "
            "employer in town."
        ),
    ),
    # --- Cheyenne (~50 mi, I-80) ---
    ShedEmployer(
        token="northrop-sentinel",
        name="Northrop Grumman (Sentinel)",
        tier=TIER_CHEYENNE,
        category="defense",
        hq_city="Cheyenne / F.E. Warren AFB",
        hq_state="WY",
        distance_mi=50,
        careers_url="https://www.northropgrumman.com/careers/sentinel-careers",
        notes=(
            "Sentinel ICBM modernization — long-funded, Rockies region. Principal "
            "Software Systems Engineer (T2/T3). Requires US citizenship + a security "
            "clearance. Workday board; filter 'Sentinel' + Wyoming."
        ),
    ),
    ShedEmployer(
        token="microsoft-cheyenne",
        name="Microsoft (Cheyenne datacenters)",
        tier=TIER_CHEYENNE,
        category="datacenter",
        hq_city="Cheyenne",
        hq_state="WY",
        distance_mi=50,
        careers_url="https://jobs.careers.microsoft.com/global/en/search?lc=Cheyenne",
        notes=(
            "Major datacenter presence. Mostly ops/infra + some SWE; ~$76–173k. "
            "A foot in a FAANG-tier employer without leaving the region."
        ),
    ),
    ShedEmployer(
        token="state-of-wyoming",
        name="State of Wyoming (ETS)",
        tier=TIER_CHEYENNE,
        category="government",
        hq_city="Cheyenne",
        hq_state="WY",
        distance_mi=50,
        careers_url="https://www.governmentjobs.com/careers/wyoming",
        notes=(
            "State Enterprise Technology Services — software/data/IT roles. NEOGOV "
            "board (governmentjobs.com); a candidate for a future auto-feed."
        ),
    ),
    ShedEmployer(
        token="custodia-bank",
        name="Custodia Bank",
        tier=TIER_CHEYENNE,
        category="fintech",
        hq_city="Cheyenne",
        hq_state="WY",
        distance_mi=50,
        careers_url="https://custodiabank.com/careers",
        notes="WY-chartered digital-asset bank. Small, technical team; fintech infra.",
    ),
    # --- Front Range, CO (~1–1.5 hr, US-287 / I-25) ---
    ShedEmployer(
        token="ursamajor",
        name="Ursa Major Technologies",
        tier=TIER_FRONT_RANGE,
        category="aerospace",
        hq_city="Berthoud",
        hq_state="CO",
        distance_mi=80,
        provider="greenhouse",
        ats_token="ursamajor",
        careers_url="https://www.ursamajor.com/careers",
        notes=(
            "Rocket propulsion. ~40 open roles, almost all onsite Berthoud + a few "
            "remote. LIVE Greenhouse feed — the radar's proof-of-concept."
        ),
    ),
    ShedEmployer(
        token="broadcom-fort-collins",
        name="Broadcom (Fort Collins ASIC)",
        tier=TIER_FRONT_RANGE,
        category="semiconductor",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        provider=PROVIDER_WORKDAY,
        ats_token="broadcom-fort-collins",
        workday=WorkdayConfig(
            tenant="broadcom", host="wd1", site="External_Career", search="Fort Collins"
        ),
        careers_url="https://www.broadcom.com/company/careers",
        notes=(
            "Custom-silicon design center. Hiring an AI Software Engineer to wire "
            "AI agents into the chip-design flow — a direct map to LLM-infra work. "
            "Live Workday feed (Fort Collins–filtered); hybrid ~2 days/wk."
        ),
    ),
    ShedEmployer(
        token="hpe-fort-collins",
        name="Hewlett Packard Enterprise",
        tier=TIER_FRONT_RANGE,
        category="bigtech",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        provider=PROVIDER_WORKDAY,
        ats_token="hpe-fort-collins",
        workday=WorkdayConfig(
            tenant="hpe", host="wd5", site="Jobsathpe", search="Fort Collins"
        ),
        careers_url="https://careers.hpe.com/us/en/search-results?keywords=Fort%20Collins",
        notes=(
            "Large Fort Collins R&D site — systems/firmware/cloud/HPC SWE. "
            "Live Workday feed (Fort Collins–filtered)."
        ),
    ),
    ShedEmployer(
        token="advanced-energy",
        name="Advanced Energy Industries",
        tier=TIER_FRONT_RANGE,
        category="hardware",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        careers_url="https://www.advancedenergy.com/en-us/about/",
        notes="Fort Collins-HQ power-conversion hardware co; embedded/controls SWE.",
    ),
    ShedEmployer(
        token="jumpcloud",
        name="JumpCloud",
        tier=TIER_FRONT_RANGE,
        category="startup",
        hq_city="Louisville",
        hq_state="CO",
        distance_mi=100,
        provider="lever",
        ats_token="jumpcloud",
        careers_url="https://jobs.lever.co/jumpcloud",
        notes=(
            "Cloud directory / IAM platform, Louisville CO (~1.5 hr, front-range "
            "edge). Remote-friendly. Live Lever feed."
        ),
    ),
    ShedEmployer(
        token="anduril-fort-collins",
        name="Anduril (ex-Numerica, Fort Collins)",
        tier=TIER_FRONT_RANGE,
        category="defense",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        provider="greenhouse",
        ats_token="andurilindustries",
        local_only=True,  # huge national board — keep only the Fort Collins office
        careers_url="https://www.anduril.com/careers",
        notes=(
            "Anduril acquired Numerica's radar + C2 business (Jan 2025) — ~100+ "
            "engineers in Fort Collins doing sensor-fusion / tracking / signal "
            "processing (C++/Python). Hot, deeply-funded defense-tech; live "
            "Greenhouse feed showing its Colorado roles (the Fort Collins office is "
            "the ex-Numerica team; national-remote roles show on /you, not here). "
            "Clearance common."
        ),
    ),
    ShedEmployer(
        token="comcast-fort-collins",
        name="Comcast (Fort Collins tech center)",
        tier=TIER_FRONT_RANGE,
        category="bigtech",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        careers_url="https://jobs.comcast.com/",
        notes="Fort Collins technology center — platform/streaming SWE. Workday.",
    ),
    ShedEmployer(
        token="nrel",
        name="NREL (Natl. Renewable Energy Lab)",
        tier=TIER_FRONT_RANGE,
        category="research",
        hq_city="Golden",
        hq_state="CO",
        distance_mi=130,
        careers_url="https://www.nrel.gov/careers/",
        notes=(
            "National lab — research software / HPC / modeling. Further (~2 hr) so "
            "hybrid-hard, but strong mission + some USAJobs-posted roles."
        ),
    ),
    # --- Wyoming-remote (domiciled in WY, remote-first) ---
    ShedEmployer(
        token="userevidence",
        name="UserEvidence",
        tier=TIER_WY_REMOTE,
        category="startup",
        hq_city="Jackson",
        hq_state="WY",
        careers_url="https://userevidence.com/careers/",
        notes=(
            "WY-domiciled B2B SaaS, remote, ~$14M raised. Hiring a Founding "
            "Principal Engineer — apply direct to the founder (evan@userevidence.com), "
            "not a portal. 'Powder policy': 7+ inches, ski AM. The warm shot."
        ),
    ),
    ShedEmployer(
        token="frontline-wildfire",
        name="Frontline Wildfire Defense",
        tier=TIER_WY_REMOTE,
        category="startup",
        hq_city="Jackson",
        hq_state="WY",
        careers_url="https://www.frontlinewildfire.com/careers/",
        notes="WY-based climate/hardware+app startup; remote-friendly eng.",
    ),
]


def role_in_shed(location: str | None, tier: str, *, local_only: bool = False) -> bool:
    """Is a role at a `tier` employer actually in reach?

    Physical-office tiers keep a role only if its location matches the tier's area
    tokens OR the role is remote. WY-remote employers are small and aligned, so
    every role passes. This is the flood-guard that stops a big employer's global
    req list from swamping the radar.

    `local_only` is for BIG NATIONAL employers (e.g. Anduril): their national-remote
    roles aren't "commute-shed" — they're just remote, better surfaced on /you — so
    only their actual local-office roles (area-token match) count here. Small/local
    employers leave it False so their remote roles still qualify.
    """
    if tier == TIER_WY_REMOTE:
        return True
    _, is_remote = classify_location(location)
    tokens = _TIER_AREA_TOKENS.get(tier, ())
    loc = (location or "").lower()
    area_match = any(t in loc for t in tokens)
    if local_only:
        return area_match
    return is_remote or area_match


def sync_registry(session: Session) -> int:
    """Upsert `SEED_EMPLOYERS` into commute_shed_employer (code is source of truth).

    Idempotent: refreshes every mutable field on conflict so editing the seed list
    propagates on the next tick. Returns the number of employers synced.
    """
    if not SEED_EMPLOYERS:
        return 0
    rows = [
        {
            "token": e.token,
            "name": e.name,
            "tier": e.tier,
            "category": e.category,
            "hq_city": e.hq_city,
            "hq_state": e.hq_state,
            "distance_mi": e.distance_mi,
            "provider": e.provider,
            "ats_token": e.ats_token,
            "careers_url": e.careers_url,
            "engineer_relevant": e.engineer_relevant,
            "notes": e.notes,
            "active": True,
            "updated_at": datetime.now(tz=UTC),
        }
        for e in SEED_EMPLOYERS
    ]
    stmt = pg_insert(CommuteShedEmployer).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[CommuteShedEmployer.token],
        set_={
            "name": stmt.excluded.name,
            "tier": stmt.excluded.tier,
            "category": stmt.excluded.category,
            "hq_city": stmt.excluded.hq_city,
            "hq_state": stmt.excluded.hq_state,
            "distance_mi": stmt.excluded.distance_mi,
            "provider": stmt.excluded.provider,
            "ats_token": stmt.excluded.ats_token,
            "careers_url": stmt.excluded.careers_url,
            "engineer_relevant": stmt.excluded.engineer_relevant,
            "notes": stmt.excluded.notes,
            "active": stmt.excluded.active,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    session.execute(stmt)
    # The seed is the source of truth: any row whose token was dropped from (or
    # renamed in) SEED_EMPLOYERS is deactivated so it stops showing on /local.
    # Soft-deactivate (not delete) to keep any accrued history intact.
    seed_tokens = [e.token for e in SEED_EMPLOYERS]
    session.execute(
        update(CommuteShedEmployer)
        .where(
            CommuteShedEmployer.token.not_in(seed_tokens),
            CommuteShedEmployer.active.is_(True),
        )
        .values(active=False)
    )
    session.commit()
    logger.info("jobtrends: commute-shed registry synced — %s employers", len(rows))
    return len(rows)


@dataclass(frozen=True)
class SnapshotResult:
    employers_ok: int
    open_roles: int
    refreshed_tokens: tuple[str, ...]  # feeds that fetched OK this run


def commute_shed_snapshot(
    session: Session,
    client: AtsClient,
    workday_client: WorkdayClient | None = None,
) -> SnapshotResult:
    """Snapshot the live-feed subset of the registry into ats_jobs.

    For each seed employer that has a feed, fetch it (Greenhouse/Lever/Ashby via the
    token board; Workday via the CXS API), keep only in-reach roles (`role_in_shed`),
    stamp source='commute_shed', upsert, then close anything that employer no longer
    lists *within this source*. One bad board is logged and skipped. Returns a
    SnapshotResult whose `refreshed_tokens` are ONLY the feeds that succeeded this
    run — so daily-stats recording can skip employers whose fetch failed (a partial
    outage must not poison the velocity history with a stale/zero count).
    """
    workday_client = workday_client or WorkdayClient()
    run_at = datetime.now(tz=UTC)
    feeds = [e for e in SEED_EMPLOYERS if e.provider and e.ats_token]
    refreshed: list[str] = []
    for e in feeds:
        try:
            if e.provider == PROVIDER_WORKDAY:
                if e.workday is None:
                    logger.warning(
                        "jobtrends: commute-shed %s is workday but has no config",
                        e.ats_token,
                    )
                    continue
                jobs = workday_client.fetch(e.ats_token, e.name, e.workday)  # type: ignore[arg-type]
            else:
                company = Company(name=e.name, provider=e.provider, token=e.ats_token)  # type: ignore[arg-type]
                jobs = client.fetch_company(company)
        except Exception:  # noqa: BLE001 — one board must not sink the snapshot
            logger.exception("jobtrends: commute-shed fetch failed for %s", e.ats_token)
            continue
        # Flood-guard + source retag. Keep only roles actually in reach. The
        # id_namespace gives these rows a distinct primary key from the national
        # 'ats' set, so a company present in BOTH streams gets one row per stream
        # (upsert never updates `source`) instead of colliding.
        kept = [
            replace(j, source=SOURCE_COMMUTE_SHED, id_namespace=SOURCE_COMMUTE_SHED)
            for j in jobs
            if role_in_shed(j.location, e.tier, local_only=e.local_only)
        ]
        upsert_jobs(session, kept, run_at)
        close_missing(
            session,
            run_at,
            provider=e.provider,  # type: ignore[arg-type]
            company_token=e.ats_token,
            source=SOURCE_COMMUTE_SHED,
        )
        session.commit()
        refreshed.append(e.ats_token)  # type: ignore[arg-type]
        logger.info(
            "jobtrends: commute-shed %s — %s/%s roles in reach",
            e.ats_token,
            len(kept),
            len(jobs),
        )

    # Close orphaned rows: commute_shed openings whose token is no longer a live
    # feed (employer dropped from the seed, or its ats_token cleared/renamed). The
    # per-employer close above only covers tokens still in `feeds`, so without this
    # those rows would linger open forever and leak into /local.
    feed_tokens = [e.ats_token for e in feeds]
    session.execute(
        update(AtsJob)
        .where(
            AtsJob.source == SOURCE_COMMUTE_SHED,
            AtsJob.is_open.is_(True),
            AtsJob.company_token.not_in(feed_tokens),
        )
        .values(is_open=False)
    )
    session.commit()

    open_roles = session.scalar(
        select(func.count())
        .select_from(AtsJob)
        .where(AtsJob.is_open.is_(True), AtsJob.source == SOURCE_COMMUTE_SHED)
    )
    logger.info(
        "jobtrends: commute-shed snapshot complete — %s feeds, %s open roles",
        len(refreshed),
        open_roles,
    )
    return SnapshotResult(
        employers_ok=len(refreshed),
        open_roles=int(open_roles or 0),
        refreshed_tokens=tuple(refreshed),
    )


def record_daily_stats(session: Session, tokens: list[str]) -> int:
    """Snapshot today's open-role count into commute_shed_stat, for `tokens` only.

    Called once per tick after the snapshot, passed ONLY the feeds that refreshed
    successfully this run (`SnapshotResult.refreshed_tokens`). Recording a failed
    feed would write a stale/zero count that then poisons the 30d velocity baseline,
    so a partial outage simply leaves a one-day gap (the baseline query tolerates
    gaps). Upserts on (captured_on, token) so repeated ticks in a day just refresh.
    Returns rows written.
    """
    if not tokens:
        return 0
    today = datetime.now(tz=UTC).date()
    counts = {
        token: int(n)
        for token, n in session.execute(
            select(AtsJob.company_token, func.count())
            .where(
                AtsJob.is_open.is_(True),
                AtsJob.source == SOURCE_COMMUTE_SHED,
                AtsJob.company_token.in_(tokens),
            )
            .group_by(AtsJob.company_token)
        ).all()
    }
    rows = [
        {"captured_on": today, "token": t, "open_roles": counts.get(t, 0)}
        for t in tokens
    ]
    stmt = pg_insert(CommuteShedStat).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[CommuteShedStat.captured_on, CommuteShedStat.token],
        set_={"open_roles": stmt.excluded.open_roles},
    )
    session.execute(stmt)
    session.commit()
    logger.info(
        "jobtrends: commute-shed daily stats recorded — %s employers", len(rows)
    )
    return len(rows)


# --- Report ---------------------------------------------------------------


@dataclass(frozen=True)
class ShedRole:
    company: str
    title: str
    location: str | None
    url: str | None
    comp_min: int | None
    comp_max: int | None
    is_new: bool  # first_seen within the trajectory window


@dataclass(frozen=True)
class ShedEmployerOut:
    token: str
    name: str
    category: str
    hq_city: str | None
    hq_state: str | None
    distance_mi: int | None
    careers_url: str
    notes: str | None
    has_feed: bool
    open_roles: int | None  # live count when we have a feed, else None (map-only)
    new_roles: int  # opened in the trajectory window (7d); 0 if no feed
    opened_30d: int  # opened in the last 30d (the momentum signal)
    # net change vs ~30 days ago from the recorded history; None until history exists.
    net_30d: int | None


@dataclass(frozen=True)
class Mover:
    token: str
    name: str
    opened_30d: int
    net_30d: int | None
    open_roles: int


@dataclass(frozen=True)
class ShedTier:
    tier: str
    label: str
    employers: list[ShedEmployerOut]
    open_roles: int  # live-feed roles across this tier


@dataclass(frozen=True)
class CommuteShedReport:
    home: str
    tiers: list[ShedTier]
    total_employers: int
    total_open_roles: int  # live commute-shed openings
    new_roles: int  # opened within the trajectory window
    movers: list[Mover]  # employers heating up (most opened in last 30d)
    roles: list[ShedRole]  # the concrete open local openings
    trajectory_days: int


def commute_shed_report(
    session: Session, *, trajectory_days: int = 7, role_limit: int = 40
) -> CommuteShedReport:
    """The radar read: registry grouped by tier with live counts + local openings.

    Live open-role counts (and a `new_roles` trajectory seed) come from ats_jobs
    rows tagged source='commute_shed', joined to the registry by ats_token. Map-only
    employers (no feed) report `open_roles=None` — a link, not a zero.
    """
    now = datetime.now(tz=UTC)
    since = now - timedelta(days=trajectory_days)
    since_30 = now - timedelta(days=30)

    employers = (
        session.execute(
            select(CommuteShedEmployer).where(CommuteShedEmployer.active.is_(True))
        )
        .scalars()
        .all()
    )
    # Only openings from ACTIVE feed employers count — a token whose employer was
    # dropped/deactivated must not surface in counts or the openings list, even in
    # the window before the snapshot's orphan-close runs.
    active_feed_tokens = [e.ats_token for e in employers if e.provider and e.ats_token]

    # Live counts per feed token: total open + opened in the 7d and 30d windows
    # (velocity from ats_jobs.first_seen — works immediately, no history needed).
    counts: dict[str, tuple[int, int, int]] = {}
    for token, total, o7, o30 in session.execute(
        select(
            AtsJob.company_token,
            func.count(),
            func.count().filter(AtsJob.first_seen >= since),
            func.count().filter(AtsJob.first_seen >= since_30),
        )
        .where(
            AtsJob.is_open.is_(True),
            AtsJob.source == SOURCE_COMMUTE_SHED,
            AtsJob.company_token.in_(active_feed_tokens),
        )
        .group_by(AtsJob.company_token)
    ).all():
        counts[token] = (int(total), int(o7 or 0), int(o30 or 0))

    # Baseline open-count ~30d ago per token, from the recorded history: the EARLIEST
    # captured_on within the last 30d (or the start of tracking if younger). net_30d
    # is None until any history exists. DISTINCT ON keeps the earliest row per token.
    baseline_30d: dict[str, int] = {}
    if active_feed_tokens:
        for token, open_then in session.execute(
            select(CommuteShedStat.token, CommuteShedStat.open_roles)
            .where(
                CommuteShedStat.captured_on >= since_30.date(),
                CommuteShedStat.token.in_(active_feed_tokens),
            )
            .distinct(CommuteShedStat.token)
            .order_by(CommuteShedStat.token, CommuteShedStat.captured_on.asc())
        ).all():
            baseline_30d[token] = int(open_then)

    by_tier: dict[str, list[ShedEmployerOut]] = {t: [] for t in TIER_ORDER}
    for e in employers:
        has_feed = bool(e.provider and e.ats_token)
        total, o7, o30 = counts.get(e.ats_token or "", (0, 0, 0))
        base = baseline_30d.get(e.ats_token or "")
        net = (total - base) if (has_feed and base is not None) else None
        by_tier.setdefault(e.tier, []).append(
            ShedEmployerOut(
                token=e.token,
                name=e.name,
                category=e.category,
                hq_city=e.hq_city,
                hq_state=e.hq_state,
                distance_mi=e.distance_mi,
                careers_url=e.careers_url,
                notes=e.notes,
                has_feed=has_feed,
                open_roles=total if has_feed else None,
                new_roles=o7 if has_feed else 0,
                opened_30d=o30 if has_feed else 0,
                net_30d=net,
            )
        )

    tiers: list[ShedTier] = []
    for t in TIER_ORDER:
        emps = sorted(
            by_tier.get(t, []),
            key=lambda x: (
                x.distance_mi if x.distance_mi is not None else 9999,
                x.name,
            ),
        )
        if not emps:
            continue
        tiers.append(
            ShedTier(
                tier=t,
                label=TIER_LABEL.get(t, t),
                employers=emps,
                open_roles=sum(e.open_roles or 0 for e in emps),
            )
        )

    # The concrete open local openings (feed rows), newest first.
    role_rows = session.execute(
        select(
            AtsJob.company_name,
            AtsJob.title,
            AtsJob.location,
            AtsJob.url,
            AtsJob.comp_min,
            AtsJob.comp_max,
            AtsJob.first_seen,
        )
        .where(
            AtsJob.is_open.is_(True),
            AtsJob.source == SOURCE_COMMUTE_SHED,
            AtsJob.company_token.in_(active_feed_tokens),
        )
        .order_by(AtsJob.first_seen.desc())
        .limit(role_limit)
    ).all()
    roles = [
        ShedRole(
            company=name,
            title=title,
            location=location,
            url=url,
            comp_min=cmin,
            comp_max=cmax,
            is_new=first_seen is not None and first_seen >= since,
        )
        for name, title, location, url, cmin, cmax, first_seen in role_rows
    ]

    total_open = sum(t.open_roles for t in tiers)
    new_roles = sum(e.new_roles for t in tiers for e in t.employers)

    # Movers: feed employers heating up, ranked by 30d opens (then net, then recent).
    # Only employers with any momentum surface — a quiet employer isn't a mover.
    movers = sorted(
        (
            Mover(
                token=e.token,
                name=e.name,
                opened_30d=e.opened_30d,
                net_30d=e.net_30d,
                open_roles=e.open_roles or 0,
            )
            for t in tiers
            for e in t.employers
            if e.has_feed and e.opened_30d > 0
        ),
        key=lambda m: (m.opened_30d, m.net_30d or 0, m.open_roles),
        reverse=True,
    )[:6]

    return CommuteShedReport(
        home="Laramie, WY",
        tiers=tiers,
        total_employers=len(employers),
        total_open_roles=total_open,
        new_roles=new_roles,
        movers=movers,
        roles=roles,
        trajectory_days=trajectory_days,
    )
