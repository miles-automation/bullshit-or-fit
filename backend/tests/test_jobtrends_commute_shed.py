"""Unit tests for the commute-shed radar (pure reach-filter + fake-session report)."""

from typing import Any

import pytest

from app.jobtrends import commute_shed as commute_shed_module
from app.jobtrends.ats import AtsClient, ParsedJob
from app.jobtrends.commute_shed import (
    SEED_EMPLOYERS,
    ShedEmployer,
    TIER_CHEYENNE,
    TIER_FRONT_RANGE,
    TIER_LARAMIE,
    TIER_WY_REMOTE,
    commute_shed_report,
    commute_shed_snapshot,
    is_tech_role_title,
    record_daily_stats,
    role_in_shed,
)
from app.jobtrends.models import CommuteShedEmployer
from app.jobtrends.workday import parse_workday
from app.jobtrends.oracle_hcm import (
    PROVIDER_ORACLE_HCM,
    OracleHcmClient,
    OracleHcmConfig,
)


# ---- id namespacing: parallel streams never collide ---------------------


def test_record_daily_stats_noop_when_nothing_refreshed() -> None:
    # A tick where every feed failed passes no tokens → record nothing (don't touch
    # the DB), so an outage day can't write a stale/zero velocity baseline.
    class _Boom:
        def execute(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("record_daily_stats must not query on empty tokens")

    assert record_daily_stats(_Boom(), []) == 0  # type: ignore[arg-type]


def test_commute_shed_is_not_a_public_source() -> None:
    # The private local stream must never be in the public-dashboard allowlist,
    # or it would surface (unlabeled) in the national /trends derived reports.
    from app.jobtrends.ats import PUBLIC_ATS_SOURCES
    from app.jobtrends.commute_shed import SOURCE_COMMUTE_SHED

    assert SOURCE_COMMUTE_SHED not in PUBLIC_ATS_SOURCES


def test_id_namespace_isolates_commute_shed_from_national_ats() -> None:
    from app.jobtrends.ats import SOURCE_ATS, ParsedJob
    from app.jobtrends.commute_shed import SOURCE_COMMUTE_SHED

    common = dict(
        provider="greenhouse",
        company_token="shared",
        company_name="Shared Co",
        external_id="123",
        title="SWE",
        location="Remote",
        department=None,
        url=None,
        content_text="",
        posted_at=None,
    )
    national = ParsedJob(**common, source=SOURCE_ATS)
    local = ParsedJob(
        **common, source=SOURCE_COMMUTE_SHED, id_namespace=SOURCE_COMMUTE_SHED
    )
    # Same physical role in both streams → distinct primary keys, no upsert fight.
    assert national.id == "greenhouse:shared:123"
    assert local.id == "commute_shed:greenhouse:shared:123"
    assert national.id != local.id


# ---- role_in_shed: the flood-guard --------------------------------------


def test_front_range_keeps_local_and_remote_rejects_far() -> None:
    assert role_in_shed("Berthoud, Colorado", TIER_FRONT_RANGE) is True
    assert role_in_shed("Fort Collins, CO", TIER_FRONT_RANGE) is True
    assert role_in_shed("Remote", TIER_FRONT_RANGE) is True  # remote is reachable
    assert role_in_shed("San Francisco, CA", TIER_FRONT_RANGE) is False
    assert role_in_shed("New York, NY", TIER_FRONT_RANGE) is False


def test_cheyenne_matches_warren_and_city() -> None:
    assert role_in_shed("Cheyenne, WY", TIER_CHEYENNE) is True
    assert role_in_shed("F.E. Warren AFB", TIER_CHEYENNE) is True
    assert role_in_shed("Denver, CO", TIER_CHEYENNE) is False


def test_local_only_drops_national_remote() -> None:
    # A big national employer (local_only) shows ONLY its local-office roles; its
    # national-remote roles belong on /you, not the place-based radar.
    assert role_in_shed("Fort Collins, CO", TIER_FRONT_RANGE, local_only=True) is True
    assert role_in_shed("Remote", TIER_FRONT_RANGE, local_only=True) is False
    # A remote posting that merely names the area is still remote → excluded.
    assert role_in_shed("Remote - Colorado", TIER_FRONT_RANGE, local_only=True) is False
    # The broad state token is dropped under local_only: far-CO ≠ the local office.
    assert (
        role_in_shed("Colorado Springs, CO", TIER_FRONT_RANGE, local_only=True) is False
    )
    assert role_in_shed("Denver, Colorado", TIER_FRONT_RANGE, local_only=True) is False
    # …but a small/local employer (default) still keeps remote and CO-wide.
    assert role_in_shed("Remote", TIER_FRONT_RANGE, local_only=False) is True
    assert role_in_shed("Denver, Colorado", TIER_FRONT_RANGE, local_only=False) is True


def test_wy_remote_keeps_everything() -> None:
    # WY-domiciled remote-first shops are small + aligned — every role passes.
    assert role_in_shed("Anywhere", TIER_WY_REMOTE) is True
    assert role_in_shed("New York, NY", TIER_WY_REMOTE) is True
    assert role_in_shed(None, TIER_WY_REMOTE) is True


def test_no_location_not_in_physical_shed() -> None:
    assert role_in_shed(None, TIER_LARAMIE) is False
    assert role_in_shed("", TIER_FRONT_RANGE) is False


# ---- the seed map is well-formed ----------------------------------------


def test_seed_has_all_tiers_and_a_live_feed() -> None:
    tiers = {e.tier for e in SEED_EMPLOYERS}
    assert {TIER_LARAMIE, TIER_CHEYENNE, TIER_FRONT_RANGE, TIER_WY_REMOTE} <= tiers
    feeds = [e for e in SEED_EMPLOYERS if e.provider and e.ats_token]
    assert feeds, "at least one employer should expose a machine feed"
    # A feed employer must carry both provider and token (snapshot needs both).
    assert all(e.provider and e.ats_token for e in feeds)
    # Every employer carries a warm link even without a feed.
    assert all(e.careers_url.startswith("http") for e in SEED_EMPLOYERS)


# ---- commute_shed_report over a fake session ----------------------------


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows

    def scalars(self) -> "_Result":
        return self


class _SeqSession:
    """Returns prepared results per execute() call, in order."""

    def __init__(self, results: list["_Result"]) -> None:
        self._it = iter(results)

    def execute(self, *_a, **_k) -> "_Result":  # noqa: ANN002, ANN003
        return next(self._it)


def _emp(token: str, tier: str, *, feed: bool, dist: int | None) -> CommuteShedEmployer:
    return CommuteShedEmployer(
        token=token,
        name=token.title(),
        tier=tier,
        category="startup",
        distance_mi=dist,
        provider="greenhouse" if feed else None,
        ats_token=token if feed else None,
        careers_url="https://example.com/careers",
        engineer_relevant=True,
        active=True,
    )


def test_report_groups_by_tier_with_live_counts_and_map_only_nulls() -> None:
    # counts query: (token, total_open, opened_7d, opened_30d)
    counts = [("ursamajor", 12, 3, 5)]
    employers = [
        _emp("ursamajor", TIER_FRONT_RANGE, feed=True, dist=80),
        _emp("broadcom", TIER_FRONT_RANGE, feed=False, dist=65),
        _emp("uw", TIER_LARAMIE, feed=False, dist=0),
    ]
    # history baseline (~30d ago): ursamajor had 8 open → net_30d = 12 - 8 = 4
    baseline = [("ursamajor", 8)]
    roles = [
        (
            "Ursamajor",
            "Propulsion SWE",
            "Berthoud, CO",
            "http://x",
            150000,
            200000,
            None,
        )
    ]
    # Query order: employers, counts, baseline history, roles.
    session = _SeqSession(
        [_Result(employers), _Result(counts), _Result(baseline), _Result(roles)]
    )

    r = commute_shed_report(session, trajectory_days=7)  # type: ignore[arg-type]

    assert r.home == "Laramie, WY"
    assert r.total_employers == 3
    assert r.total_open_roles == 12
    assert r.new_roles == 3
    # Tiers are ordered near->far; Laramie before Front Range.
    assert [t.tier for t in r.tiers] == [TIER_LARAMIE, TIER_FRONT_RANGE]

    front = next(t for t in r.tiers if t.tier == TIER_FRONT_RANGE)
    # Within a tier, sorted by distance: Broadcom (65) before Ursa Major (80).
    assert [e.token for e in front.employers] == ["broadcom", "ursamajor"]
    ursa = next(e for e in front.employers if e.token == "ursamajor")
    broad = next(e for e in front.employers if e.token == "broadcom")
    assert ursa.has_feed is True and ursa.open_roles == 12 and ursa.new_roles == 3
    # Velocity: 5 opened in 30d, net +4 vs the recorded baseline.
    assert ursa.opened_30d == 5 and ursa.net_30d == 4
    # Map-only employer: a link, not a zero, and no velocity.
    assert broad.has_feed is False and broad.open_roles is None and broad.new_roles == 0
    assert broad.opened_30d == 0 and broad.net_30d is None

    # Movers: only feed employers with 30d momentum surface.
    assert [m.token for m in r.movers] == ["ursamajor"]
    assert r.movers[0].opened_30d == 5 and r.movers[0].net_30d == 4

    assert len(r.roles) == 1 and r.roles[0].company == "Ursamajor"


# ---- university feeds ---------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Research Computing & Systems Administrator",
        "IT Support Technician I",
        "26-124 Research Data Engineer/Statistical Programmer Part-time",
        "Open Source Forest Data Analyst",
        "Reporting Engineer",
        "Student Information Systems Administrator",
        "Non-Student Hourly - IT Support Technician",
    ],
)
def test_university_filter_keeps_real_tech_titles(title: str) -> None:
    assert is_tech_role_title(title)


@pytest.mark.parametrize(
    "title",
    [
        "Radiology Technologist - R.T.",
        "Advanced Imaging Technologist (MRI)-2",
        "Licensing Manager - Technology Transfer Office",
        "Hourly Pooled - IT Maint. Technician Assistant",
        "Work Study - Data Assistant - WYNDD",
        "Hourly Pooled-Event Technology Intern",
        "Engineer, Senior - Facilities Engineering",
        "Facilities Maintenance Technician – Mechanical Systems",
        "Make it happen coordinator",
        "Thermal Systems Engineer",
        "Process Systems Engineer",
        "Power Systems Engineer",
        "Student Hourly - IT Support Technician",
        "Student Web Developer",
    ],
)
def test_university_filter_drops_non_tech_titles(title: str) -> None:
    assert not is_tech_role_title(title)


def test_universities_are_registered_with_live_tech_filtered_feeds() -> None:
    by_token = {e.token: e for e in SEED_EMPLOYERS}
    uw = by_token["university-of-wyoming"]
    csu = by_token["colorado-state-university"]
    assert uw.provider == PROVIDER_ORACLE_HCM and uw.oracle_hcm is not None
    assert uw.tier == TIER_LARAMIE and uw.tech_only
    assert csu.workday is not None and csu.tier == TIER_FRONT_RANGE
    assert csu.tech_only and csu.local_only


def test_csfs_durango_is_not_in_reach_for_csu() -> None:
    csu = next(e for e in SEED_EMPLOYERS if e.token == "colorado-state-university")
    assert csu.workday is not None
    payload = {
        "jobPostings": [
            {"title": "GIS Analyst", "externalPath": "/job/CSFS-Durango/GIS_1"},
            {"title": "IT Support", "externalPath": "/job/Sage-Hall/IT_2"},
        ]
    }
    locations = [j.location for j in parse_workday("c", "C", csu.workday, payload)]
    reach = [
        role_in_shed(loc, csu.tier, local_only=csu.local_only) for loc in locations
    ]
    assert reach == [False, True]


def _uw_job(title: str, location: str) -> ParsedJob:
    return ParsedJob(
        provider=PROVIDER_ORACLE_HCM,
        company_token="good",
        company_name="Good",
        external_id=title,
        title=title,
        location=location,
        department=None,
        url=None,
        content_text=title,
        posted_at=None,
    )


def _oracle_employer(token: str) -> ShedEmployer:
    return ShedEmployer(
        token=token,
        name=token,
        tier=TIER_LARAMIE,
        category="university",
        careers_url="https://example.com",
        provider=PROVIDER_ORACLE_HCM,
        ats_token=token,
        oracle_hcm=OracleHcmConfig(host=f"{token}.example.com", site="CX_1"),
        tech_only=True,
    )


class _SnapshotSession:
    def execute(self, _stmt: Any) -> None:
        return None

    def commit(self) -> None:
        return None

    def scalar(self, _stmt: Any) -> int:
        return 0


class _FlakyOracleClient(OracleHcmClient):
    def fetch(
        self, company_token: str, company_name: str, cfg: OracleHcmConfig
    ) -> list[ParsedJob]:
        if company_token == "bad":
            raise RuntimeError("boom")
        return [
            _uw_job("Systems Administrator", "Laramie, WY, United States"),
            _uw_job("Software Developer", "Powell, WY, United States"),
            _uw_job("Assistant Professor - Zoology", "Laramie, WY, United States"),
        ]


def test_snapshot_filters_and_isolates_a_failed_employer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upserted: list[str] = []
    closed: list[str] = []

    def fake_upsert(_session: Any, jobs: list[ParsedJob], _run_at: Any) -> None:
        upserted.extend(j.title for j in jobs)

    def fake_close(_session: Any, _run_at: Any, **kwargs: Any) -> None:
        closed.append(kwargs["company_token"])

    monkeypatch.setattr(
        commute_shed_module,
        "SEED_EMPLOYERS",
        [_oracle_employer("bad"), _oracle_employer("good")],
    )
    monkeypatch.setattr(commute_shed_module, "upsert_jobs", fake_upsert)
    monkeypatch.setattr(commute_shed_module, "close_missing", fake_close)

    result = commute_shed_snapshot(
        _SnapshotSession(),  # type: ignore[arg-type]
        AtsClient(),
        oracle_hcm_client=_FlakyOracleClient(),
    )

    assert upserted == ["Systems Administrator"]
    assert closed == ["good"]
    assert result.refreshed_tokens == ("good",)


def test_trihydro_feed_reaches_front_range_cheyenne_and_open_roles() -> None:
    from app.jobtrends.commute_shed import in_reach
    from app.jobtrends.hrsmart import PROVIDER_HRSMART

    tri = next(e for e in SEED_EMPLOYERS if e.token == "trihydro")
    assert tri.provider == PROVIDER_HRSMART and tri.hrsmart is not None
    assert tri.tech_only
    assert in_reach(tri, "Fort Collins, CO, US / Laramie, WY, US")
    assert in_reach(tri, "Fort Collins, CO, US")
    assert in_reach(tri, "Cheyenne, WY, US")
    assert in_reach(tri, "Open, US")
    assert not in_reach(tri, "Anaconda, MT, US 59711")
    assert not in_reach(tri, "Salt Lake City, UT, US 84044")
    assert not in_reach(tri, "Cheyenne Wells, CO, US")
    assert not in_reach(tri, "Fort Collinsville, TX, US")


def test_tech_filter_keeps_software_titles_that_mention_hardware_systems() -> None:
    assert is_tech_role_title("Senior Software Engineer - Control Systems")
    assert is_tech_role_title("Senior Systems Engineer")
    assert is_tech_role_title("Business Process Systems Analyst")
    assert is_tech_role_title("Process Systems Administrator")
    assert not is_tech_role_title("Thermal Systems Engineering Lead")


def test_small_employers_are_registered_with_unique_tokens() -> None:
    tokens = [e.token for e in SEED_EMPLOYERS]
    assert len(tokens) == len(set(tokens))
    feed_keys = [(e.provider, e.ats_token) for e in SEED_EMPLOYERS if e.provider]
    assert len(feed_keys) == len(set(feed_keys))
    for token in ("handel-it", "tilt", "west-inc", "wilqo", "atmoszero", "sparkfun"):
        assert token in tokens


def test_fully_remote_employers_keep_roles_listed_at_a_distant_office() -> None:
    from app.jobtrends.commute_shed import in_reach

    by_token = {e.token: e for e in SEED_EMPLOYERS}
    assert in_reach(by_token["tilt"], "Denver, CO")
    assert in_reach(by_token["hudu"], "United States")
    assert not in_reach(by_token["turbotenant"], "Knoxville, TN")
