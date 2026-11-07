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
import re
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
from app.jobtrends.hiringthing import (
    PROVIDER_HIRINGTHING,
    HiringThingClient,
    HiringThingConfig,
)
from app.jobtrends.hrsmart import PROVIDER_HRSMART, HrsmartClient, HrsmartConfig
from app.jobtrends.neogov import PROVIDER_NEOGOV, NeogovClient, NeogovConfig
from app.jobtrends.oracle_hcm import (
    PROVIDER_ORACLE_HCM,
    OracleHcmClient,
    OracleHcmConfig,
)
from app.jobtrends.rippling import PROVIDER_RIPPLING, RipplingClient, RipplingConfig
from app.jobtrends.workable import PROVIDER_WORKABLE, WorkableClient, WorkableConfig
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

# Broad state/region tokens that match a whole state, not a commutable metro. They're
# fine for small local employers, but under `local_only` (big national boards) they'd
# admit far-CO postings (Colorado Springs, Denver) that aren't the local office, so
# local_only matches only the specific-metro tokens.
_BROAD_AREA_TOKENS = frozenset({"colorado"})

_TECH_TITLE_RE = re.compile(
    r"\b(?:software|developer|programmer|(?-i:IT)|information technology|"
    r"information security|cyber\w*|research computing|computer science|"
    r"computer information|computing|hpc|database|devops|cloud engineer|"
    r"machine learning|artificial intelligence|business intelligence|etl|gis|"
    r"data (?:analyst|engineer|scientist|architect|manager)|reporting engineer|"
    r"systems? (?:analyst|administrator|engineer|programmer|architect)|"
    r"network (?:engineer|administrator|analyst)|web developer|webmaster)\b",
    re.IGNORECASE,
)
_STUDENT_ROLE_RE = re.compile(
    r"^\s*(?:work study|hourly pooled)\b|\b(?:intern|internship)\b|"
    r"(?<!non-)\bstudent (?:hourly|assistant|employee|worker|technician|developer|"
    r"programmer|web developer)\b",
    re.IGNORECASE,
)


_NON_SOFTWARE_SYSTEMS_RE = re.compile(
    r"\b(?:thermal|process|mechanical|electrical|hvac|power|fluid|propulsion)"
    r" systems? engineer\w*\b",
    re.IGNORECASE,
)


def is_tech_role_title(title: str) -> bool:
    cleaned = _NON_SOFTWARE_SYSTEMS_RE.sub(" ", title)
    return bool(_TECH_TITLE_RE.search(cleaned)) and not _STUDENT_ROLE_RE.search(title)


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
    # NEOGOV (governmentjobs.com) gov employers: provider='neogov' + this config;
    # ats_token must equal the agency slug (that's the job rows' company_token).
    neogov: NeogovConfig | None = None
    oracle_hcm: OracleHcmConfig | None = None
    hrsmart: HrsmartConfig | None = None
    rippling: RipplingConfig | None = None
    workable: WorkableConfig | None = None
    hiringthing: HiringThingConfig | None = None
    extra_reach_tokens: tuple[str, ...] = ()
    fully_remote: bool = False
    tech_only: bool = False
    # Big national employers: keep only their local-office roles (drop national-remote
    # ones, which belong on /you, not this place-based radar).
    local_only: bool = False
    engineer_relevant: bool = True
    notes: str | None = None


# --- The curated map -------------------------------------------------------
# Real employers reachable from Laramie, WY. Live feeds: Ursa Major + Anduril/
# ex-Numerica (Greenhouse), JumpCloud (Lever), Broadcom + HPE + Colorado State
# (Workday CXS, Fort-Collins-filtered), State of Wyoming (NEOGOV, tech-filtered — the
# local long tail), and University of Wyoming (Oracle HCM, tech-filtered). The rest are Taleo/iCIMS/private, so they're map-only (careers_url +
# notes) until a feed is found. Watch for acquisitions (Teton→Markforged,
# Numerica→Anduril) staling careers URLs. Add rows freely — sync picks them up; a
# token dropped here is auto-deactivated.
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
        provider=PROVIDER_ORACLE_HCM,
        ats_token="university-of-wyoming",
        oracle_hcm=OracleHcmConfig(host="eeik.fa.us2.oraclecloud.com", site="CX_1"),
        tech_only=True,
        careers_url="https://eeik.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1",
        notes=(
            "The anchor employer in town. School of Computing + ARCC research "
            "computing. Live Oracle HCM feed, tech roles in Laramie only; student "
            "jobs excluded. Steady, benefits, in-person."
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
        provider=PROVIDER_HRSMART,
        ats_token="trihydro",
        hrsmart=HrsmartConfig(host="trihydro.hua.hrsmart.com"),
        tech_only=True,
        extra_reach_tokens=("cheyenne, wy", "fort collins, co", "open, us"),
        careers_url="https://trihydro.hua.hrsmart.com/hr/ats/JobSearch/viewAll",
        notes=(
            "Large Laramie-HQ environmental/engineering firm (~700 staff). "
            "In-house software/GIS/data roles, including connected-vehicle "
            "software. Live Deltek (hrsmart) feed, tech roles in Laramie, "
            "Cheyenne, Fort Collins or open location."
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
        name="State of Wyoming",
        tier=TIER_CHEYENNE,
        category="government",
        hq_city="Cheyenne",
        hq_state="WY",
        distance_mi=50,
        provider=PROVIDER_NEOGOV,
        ats_token="wyoming",  # governmentjobs.com agency slug = the row company_token
        neogov=NeogovConfig(agency="wyoming"),
        careers_url="https://www.governmentjobs.com/careers/wyoming",
        notes=(
            "State gov — software/data/IT/GIS/systems roles across ~320 postings. "
            "Live NEOGOV feed, filtered to tech roles in the Cheyenne area. Steady, "
            "benefits, no clearance."
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
        token="colorado-state-university",
        name="Colorado State University",
        tier=TIER_FRONT_RANGE,
        category="university",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        provider=PROVIDER_WORKDAY,
        ats_token="colorado-state-university",
        workday=WorkdayConfig(
            tenant="csusystem",
            host="wd12",
            site="fortcollins_careers",
            campus_city="Fort Collins",
            campus_locations=frozenset(
                {
                    "Behavioral Sciences Building",
                    "Biology",
                    "Clark Hall",
                    "Guggenheim Hall",
                    "Moby",
                    "Painter Center",
                    "Pathology",
                    "Plant Sciences",
                    "Rockwell Hall",
                    "Sage Hall",
                    "Social Sciences Hall",
                    "Translational Medicine Institute Tmi",
                    "University Center for the Arts UCA",
                    "Veterinary Hospital and Education Complex VTH",
                }
            ),
        ),
        tech_only=True,
        local_only=True,
        careers_url="https://csusystem.wd12.myworkdayjobs.com/fortcollins_careers",
        notes=(
            "Research computing, IT and research data roles. Live Workday feed, tech "
            "roles at the Fort Collins campus only; far-flung CEMML sites and student "
            "jobs excluded."
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
        careers_url="https://jobs.advanced-energy.com/",
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
            "Greenhouse feed pinned to the Fort Collins / NoCo office roles (the "
            "ex-Numerica team; far-CO + national-remote roles show on /you, not "
            "here). Clearance common."
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
    # --- Small local employers (research 2026-11-07, task 1091) ---
    ShedEmployer(
        token="handel-it",
        name="Handel Information Technologies",
        tier=TIER_LARAMIE,
        category="software",
        hq_city="Laramie",
        hq_state="WY",
        distance_mi=0,
        careers_url="https://www.handelit.com/about/careers/",
        notes="RiteTrack justice case-management SaaS, about 27 staff. C#, TypeScript, SQL Server. Posts openings on a plain page and hires by email.",
    ),
    ShedEmployer(
        token="uplink-robotics",
        name="UplinkRobotics",
        tier=TIER_LARAMIE,
        category="startup",
        hq_city="Laramie",
        hq_state="WY",
        distance_mi=0,
        careers_url="https://uplinkrobotics.com/careers/",
        notes="Inspection crawler robots, about 7 staff, IMPACT 307 graduate. Rarely hires software.",
    ),
    ShedEmployer(
        token="soil-action",
        name="Soil Action",
        tier=TIER_LARAMIE,
        category="startup",
        hq_city="Laramie",
        hq_state="WY",
        distance_mi=0,
        careers_url="https://soilaction.com/",
        notes="Seed-stage soil chemistry sensor plus AI for farms. No careers page found.",
    ),
    ShedEmployer(
        token="mountain-west-farm-bureau",
        name="Mountain West Farm Bureau Insurance",
        tier=TIER_LARAMIE,
        category="insurance",
        hq_city="Laramie",
        hq_state="WY",
        distance_mi=0,
        careers_url="https://mwfbic.com/careers/",
        notes="Regional insurer with its home office in Laramie; occasional IT and production support roles.",
    ),
    ShedEmployer(
        token="uniwyo",
        name="UniWyo Federal Credit Union",
        tier=TIER_LARAMIE,
        category="finance",
        hq_city="Laramie",
        hq_state="WY",
        distance_mi=0,
        careers_url="https://www.uniwyo.com/Connect/About/Careers",
        notes="Laramie credit union; occasional technology leadership roles.",
    ),
    ShedEmployer(
        token="west-inc",
        name="WEST (Western EcoSystems Technology)",
        tier=TIER_CHEYENNE,
        category="consulting",
        hq_city="Cheyenne",
        hq_state="WY",
        distance_mi=50,
        provider=PROVIDER_HRSMART,
        ats_token="west-inc",
        hrsmart=HrsmartConfig(host="west-inc.hua.hrsmart.com"),
        tech_only=True,
        extra_reach_tokens=("laramie, wy", "fort collins, co", "open, us"),
        careers_url="https://west-inc.com/careers/",
        notes="Ecological and statistical consulting with offices in Cheyenne, Laramie and Fort Collins; hires data scientists and software developers. Live Deltek (hrsmart) feed, tech roles only.",
    ),
    ShedEmployer(
        token="language-io",
        name="Language I/O",
        tier=TIER_CHEYENNE,
        category="startup",
        hq_city="Cheyenne",
        hq_state="WY",
        distance_mi=50,
        careers_url="https://languageio.com/careers/",
        notes="LLM and machine translation for customer support, about 42 staff. No listings on its site; roles go to LinkedIn.",
    ),
    ShedEmployer(
        token="gannett-trust",
        name="Gannett Trust Company",
        tier=TIER_CHEYENNE,
        category="fintech",
        hq_city="Cheyenne",
        hq_state="WY",
        distance_mi=50,
        provider=PROVIDER_RIPPLING,
        ats_token="gannett-trust",
        rippling=RipplingConfig(board="gannett"),
        tech_only=True,
        careers_url="https://ats.rippling.com/gannett/jobs",
        notes="Wyoming trust company with digital asset custody, about 10 staff. Live Rippling feed, tech roles only.",
    ),
    ShedEmployer(
        token="powder-river-industries",
        name="Powder River Industries",
        tier=TIER_CHEYENNE,
        category="government",
        hq_city="Cheyenne",
        hq_state="WY",
        distance_mi=50,
        careers_url="https://www.powderriverindustries.com/employment",
        notes="SBA 8(a) federal IT contractor based in Cheyenne; current postings are on-site in Washington, DC.",
    ),
    ShedEmployer(
        token="tilt",
        name="TiLT",
        tier=TIER_FRONT_RANGE,
        category="startup",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        provider=PROVIDER_HIRINGTHING,
        ats_token="tilt",
        hiringthing=HiringThingConfig(host="hellotilt.rippling-ats.com"),
        fully_remote=True,
        careers_url="https://hellotilt.com/careers/",
        notes="Leave-of-absence management software, about 72 staff, fully remote. Postings list Denver, CO as location. Live HiringThing RSS feed.",
    ),
    ShedEmployer(
        token="spirae",
        name="Spirae",
        tier=TIER_FRONT_RANGE,
        category="software",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        careers_url="https://spirae.com/about",
        notes="Microgrid and distributed energy control software, about 29 staff. Job notices on the About page.",
    ),
    ShedEmployer(
        token="hudu",
        name="Hudu",
        tier=TIER_FRONT_RANGE,
        category="startup",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        provider="ashby",
        ats_token="hudu",
        fully_remote=True,
        careers_url="https://www.hudu.com/careers",
        notes="IT documentation platform for managed service providers, about 23 staff, fully remote postings. Live Ashby feed.",
    ),
    ShedEmployer(
        token="billgo",
        name="BillGO",
        tier=TIER_FRONT_RANGE,
        category="fintech",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        careers_url="https://billgo.com/join-us",
        notes="Bill pay network for banks and small businesses, about 175 staff. Paycom postings linked from its careers page.",
    ),
    ShedEmployer(
        token="encompass-technologies",
        name="Encompass Technologies",
        tier=TIER_FRONT_RANGE,
        category="software",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        provider="greenhouse",
        ats_token="encompasstechnologiesllp",
        careers_url="https://www.encompasstech.com/careers/jobs",
        notes="Route accounting and ERP software for beverage distributors, 200 to 230 staff. Live Greenhouse feed.",
    ),
    ShedEmployer(
        token="turbotenant",
        name="TurboTenant",
        tier=TIER_FRONT_RANGE,
        category="startup",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        provider="greenhouse",
        ats_token="turbotenant",
        careers_url="https://www.turbotenant.com/careers/",
        notes="Landlord and rental management software, about 180 staff. Live Greenhouse feed.",
    ),
    ShedEmployer(
        token="canidium",
        name="Canidium",
        tier=TIER_FRONT_RANGE,
        category="consulting",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        careers_url="https://www.canidium.com/careers",
        notes="Sales performance and pricing consultancy, about 120 staff; mostly consultants and contractors. CATS job board.",
    ),
    ShedEmployer(
        token="remote-geosystems",
        name="Remote GeoSystems",
        tier=TIER_FRONT_RANGE,
        category="hardware",
        hq_city="Fort Collins",
        hq_state="CO",
        distance_mi=65,
        careers_url="https://remotegeo.com/careers/",
        notes="Geotagged video hardware and GIS mapping software, about 5 staff.",
    ),
    ShedEmployer(
        token="atmoszero",
        name="AtmosZero",
        tier=TIER_FRONT_RANGE,
        category="hardware",
        hq_city="Loveland",
        hq_state="CO",
        distance_mi=75,
        provider=PROVIDER_RIPPLING,
        ats_token="atmoszero",
        rippling=RipplingConfig(board="atmoszero-careers"),
        tech_only=True,
        careers_url="https://ats.rippling.com/atmoszero-careers/jobs",
        notes="Electric industrial steam heat pumps. Live Rippling feed, tech roles only.",
    ),
    ShedEmployer(
        token="sparkfun",
        name="SparkFun Electronics",
        tier=TIER_FRONT_RANGE,
        category="hardware",
        hq_city="Niwot",
        hq_state="CO",
        distance_mi=100,
        provider=PROVIDER_WORKABLE,
        ats_token="sparkfun",
        workable=WorkableConfig(account="sparkfun-electronics"),
        tech_only=True,
        extra_reach_tokens=("niwot",),
        careers_url="https://www.sparkfun.com/jobs",
        notes="Open-source electronics maker with its own e-commerce platform. Live Workable feed, tech roles only.",
    ),
    ShedEmployer(
        token="atomcomputing",
        name="Atom Computing",
        tier=TIER_FRONT_RANGE,
        category="startup",
        hq_city="Boulder",
        hq_state="CO",
        distance_mi=110,
        provider="lever",
        ats_token="atomcomputing",
        tech_only=True,
        careers_url="https://atom-computing.com/careers/",
        notes="Neutral-atom quantum computers, about 130 staff; software roles in Boulder. Live Lever feed, tech roles only.",
    ),
    ShedEmployer(
        token="campminder",
        name="CampMinder",
        tier=TIER_FRONT_RANGE,
        category="startup",
        hq_city="Boulder",
        hq_state="CO",
        distance_mi=110,
        provider="greenhouse",
        ats_token="campminder",
        careers_url="https://campminder.com/careers/",
        notes="Camp management SaaS, about 93 staff. Live Greenhouse feed.",
    ),
    ShedEmployer(
        token="wilqo",
        name="Wilqo",
        tier=TIER_WY_REMOTE,
        category="fintech",
        hq_city="Ten Sleep",
        hq_state="WY",
        careers_url="https://builtin.com/company/wilqo/jobs",
        notes="Mortgage lending platform, about 54 staff, .NET Core, React and event sourcing. No careers page; roles post on Built In and LinkedIn.",
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
    if local_only:
        # National employer → only a genuine local office: match the specific-metro
        # tokens (not the broad state catch-all, which would admit Denver/Colo
        # Springs) AND exclude anything remote (even "Remote - Colorado").
        office_tokens = (t for t in tokens if t not in _BROAD_AREA_TOKENS)
        return not is_remote and any(t in loc for t in office_tokens)
    return is_remote or any(t in loc for t in tokens)


def in_reach(employer: ShedEmployer, location: str | None) -> bool:
    if employer.fully_remote:
        return True
    entries = [e.strip().lower() for e in (location or "").split(" / ")]
    return role_in_shed(location, employer.tier, local_only=employer.local_only) or any(
        entry.startswith(token)
        for entry in entries
        for token in employer.extra_reach_tokens
    )


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
    neogov_client: NeogovClient | None = None,
    oracle_hcm_client: OracleHcmClient | None = None,
    hrsmart_client: HrsmartClient | None = None,
    rippling_client: RipplingClient | None = None,
    workable_client: WorkableClient | None = None,
    hiringthing_client: HiringThingClient | None = None,
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
    neogov_client = neogov_client or NeogovClient()
    oracle_hcm_client = oracle_hcm_client or OracleHcmClient()
    hrsmart_client = hrsmart_client or HrsmartClient()
    rippling_client = rippling_client or RipplingClient()
    workable_client = workable_client or WorkableClient()
    hiringthing_client = hiringthing_client or HiringThingClient()
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
            elif e.provider == PROVIDER_NEOGOV:
                if e.neogov is None:
                    logger.warning(
                        "jobtrends: commute-shed %s is neogov but has no config",
                        e.ats_token,
                    )
                    continue
                jobs = neogov_client.fetch(e.neogov)
            elif e.provider == PROVIDER_ORACLE_HCM:
                if e.oracle_hcm is None:
                    logger.warning(
                        "jobtrends: commute-shed %s is oracle_hcm but has no config",
                        e.ats_token,
                    )
                    continue
                jobs = oracle_hcm_client.fetch(e.ats_token, e.name, e.oracle_hcm)  # type: ignore[arg-type]
            elif e.provider == PROVIDER_HRSMART:
                if e.hrsmart is None:
                    logger.warning(
                        "jobtrends: commute-shed %s is hrsmart but has no config",
                        e.ats_token,
                    )
                    continue
                jobs = hrsmart_client.fetch(e.ats_token, e.name, e.hrsmart)  # type: ignore[arg-type]
            elif e.provider == PROVIDER_RIPPLING:
                if e.rippling is None:
                    logger.warning(
                        "jobtrends: commute-shed %s is rippling but has no config",
                        e.ats_token,
                    )
                    continue
                jobs = rippling_client.fetch(e.ats_token, e.name, e.rippling)  # type: ignore[arg-type]
            elif e.provider == PROVIDER_WORKABLE:
                if e.workable is None:
                    logger.warning(
                        "jobtrends: commute-shed %s is workable but has no config",
                        e.ats_token,
                    )
                    continue
                jobs = workable_client.fetch(e.ats_token, e.name, e.workable)  # type: ignore[arg-type]
            elif e.provider == PROVIDER_HIRINGTHING:
                if e.hiringthing is None:
                    logger.warning(
                        "jobtrends: commute-shed %s is hiringthing but has no config",
                        e.ats_token,
                    )
                    continue
                jobs = hiringthing_client.fetch(e.ats_token, e.name, e.hiringthing)  # type: ignore[arg-type]
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
            if in_reach(e, j.location)
            and (not e.tech_only or is_tech_role_title(j.title))
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
