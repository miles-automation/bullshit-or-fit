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
from app.jobtrends.models import AtsJob, CommuteShedEmployer

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
    provider: str | None = None  # greenhouse | lever | ashby, when a feed exists
    ats_token: str | None = None
    engineer_relevant: bool = True
    notes: str | None = None


# --- The curated map -------------------------------------------------------
# Real employers reachable from Laramie, WY. Feeds verified live 2026-07-08:
# only Ursa Major (Greenhouse) currently exposes a machine board; the rest are
# Workday/Taleo/iCIMS or private, so they're map-only (careers_url + notes) until
# a token is discovered. Add rows freely — the sync picks them up next tick.
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
        token="teton-simulation",
        name="Teton Simulation Software",
        tier=TIER_LARAMIE,
        category="startup",
        hq_city="Laramie",
        hq_state="WY",
        distance_mi=0,
        careers_url="https://www.tetonsim.com/company/careers",
        notes="UW-spinout; additive-manufacturing simulation (SmartSlice). Small eng team.",
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
        careers_url="https://www.broadcom.com/company/careers/search",
        notes=(
            "Custom-silicon design center. Hiring an AI Software Engineer to wire "
            "AI agents into the chip-design flow — a direct map to LLM-infra work. "
            "Workday board; hybrid ~2 days/wk."
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
        careers_url="https://careers.hpe.com/us/en/search-results?keywords=Fort%20Collins",
        notes="Large Fort Collins R&D site — systems/firmware/cloud SWE. Workday.",
    ),
    ShedEmployer(
        token="advanced-energy",
        name="Advanced Energy Industries",
        tier=TIER_FRONT_RANGE,
        category="hardware",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        careers_url="https://careers.advancedenergy.com/",
        notes="Fort Collins-HQ power-conversion hardware co; embedded/controls SWE.",
    ),
    ShedEmployer(
        token="numerica",
        name="Numerica Corporation",
        tier=TIER_FRONT_RANGE,
        category="defense",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        careers_url="https://www.numerica.us/careers/",
        notes=(
            "Defense sensor-fusion / tracking algorithms — heavy math + C++/Python. "
            "Clearance common. A serious-engineering shop an hour away."
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


def role_in_shed(location: str | None, tier: str) -> bool:
    """Is a role at a `tier` employer actually in reach?

    Physical-office tiers keep a role only if its location matches the tier's area
    tokens OR the role is remote. WY-remote employers are small and aligned, so
    every role passes. This is the flood-guard that stops a big employer's global
    req list from swamping the radar.
    """
    if tier == TIER_WY_REMOTE:
        return True
    _, is_remote = classify_location(location)
    if is_remote:
        return True
    tokens = _TIER_AREA_TOKENS.get(tier, ())
    loc = (location or "").lower()
    return any(t in loc for t in tokens)


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


def commute_shed_snapshot(session: Session, client: AtsClient) -> dict[str, int]:
    """Snapshot the live-feed subset of the registry into ats_jobs.

    For each seed employer that has a (provider, ats_token), fetch its board, keep
    only in-reach roles (`role_in_shed`), stamp source='commute_shed', upsert, then
    close anything that employer no longer lists *within this source*. One bad board
    is logged and skipped. Returns {employers_ok, open_roles}.
    """
    run_at = datetime.now(tz=UTC)
    feeds = [e for e in SEED_EMPLOYERS if e.provider and e.ats_token]
    ok = 0
    for e in feeds:
        company = Company(name=e.name, provider=e.provider, token=e.ats_token)  # type: ignore[arg-type]
        try:
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
            if role_in_shed(j.location, e.tier)
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
        ok += 1
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
        ok,
        open_roles,
    )
    return {"employers_ok": ok, "open_roles": int(open_roles or 0)}


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
    new_roles: int  # opened within the trajectory window (0 if no feed)


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
    since = datetime.now(tz=UTC) - timedelta(days=trajectory_days)

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

    # Live counts per feed token: total open + how many opened in the window.
    counts: dict[str, tuple[int, int]] = {}
    for token, total, fresh in session.execute(
        select(
            AtsJob.company_token,
            func.count(),
            func.count().filter(AtsJob.first_seen >= since),
        )
        .where(
            AtsJob.is_open.is_(True),
            AtsJob.source == SOURCE_COMMUTE_SHED,
            AtsJob.company_token.in_(active_feed_tokens),
        )
        .group_by(AtsJob.company_token)
    ).all():
        counts[token] = (int(total), int(fresh or 0))

    by_tier: dict[str, list[ShedEmployerOut]] = {t: [] for t in TIER_ORDER}
    for e in employers:
        has_feed = bool(e.provider and e.ats_token)
        total, fresh = counts.get(e.ats_token or "", (0, 0))
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
                new_roles=fresh if has_feed else 0,
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
    return CommuteShedReport(
        home="Laramie, WY",
        tiers=tiers,
        total_employers=len(employers),
        total_open_roles=total_open,
        new_roles=new_roles,
        roles=roles,
        trajectory_days=trajectory_days,
    )
