"""Unit tests for geography classification + rollup (DB-free)."""

from app.jobtrends.geo import classify_location, format_geo, geo_report


# ---- classify_location (over real-shape prod strings) ---------------------


def test_classify_metro_only() -> None:
    assert classify_location("San Francisco, CA") == ("SF Bay Area", False)
    assert classify_location("Seattle, Washington") == ("Seattle", False)
    assert classify_location("Washington, District of Columbia") == (
        "Washington DC",
        False,
    )


def test_classify_metro_wins_over_remote_but_flags_remote() -> None:
    # "San Francisco, CA, US; Remote, US" -> metro bucket + remote flag.
    bucket, is_remote = classify_location("San Francisco, CA, US; Remote, US")
    assert bucket == "SF Bay Area"
    assert is_remote is True


def test_classify_pure_remote() -> None:
    assert classify_location("Remote - USA") == ("Remote", True)
    assert classify_location("Remote US") == ("Remote", True)
    assert classify_location("Worldwide") == ("Remote", True)


def test_classify_various_and_other() -> None:
    assert classify_location("Multiple Locations") == ("Various", False)
    assert classify_location("Location Negotiable After Selection") == (
        "Various",
        False,
    )
    assert classify_location("United States") == ("Other", False)
    assert classify_location(None) == ("Other", False)


def test_seattle_not_captured_by_dc_washington() -> None:
    # Regression: "Washington" state city must not fall into Washington DC.
    assert classify_location("Seattle, Washington")[0] == "Seattle"


def test_dc_area_abbreviated_forms() -> None:
    # Regression (Codex): aliases must match the common abbreviated forms, not just
    # the spelled-out ones — a trailing \b after "d"/"v" clipped "DC"/"VA".
    for s in ("Washington, DC", "Washington D.C.", "Arlington, VA", "Alexandria, VA"):
        assert classify_location(s)[0] == "Washington DC", s
    # ...but a same-named city in another state must not be captured.
    assert classify_location("Arlington, Texas")[0] == "Other"


def test_cambridge_massachusetts_is_boston() -> None:
    assert classify_location("Cambridge, Massachusetts")[0] == "Boston"
    assert classify_location("Cambridge, MA")[0] == "Boston"


# ---- geo_report (fake session over location_stat) -------------------------


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def execute(self, *_a, **_k) -> _Result:  # noqa: ANN002, ANN003
        return _Result(self._rows)


# (source, bucket, n_roles, remote_roles)
ROWS = [
    ("ats", "SF Bay Area", 900, 300),
    ("ats", "New York", 500, 200),
    ("ats", "Remote", 800, 800),
    ("ats", "Other", 400, 0),
    ("usajobs", "Washington DC", 517, 0),
    ("usajobs", "Various", 1338, 0),
]


def test_geo_report_ranks_metros_and_remote_pct() -> None:
    report = geo_report(_FakeSession(ROWS))
    ats = next(g for g in report if g.source == "ats")
    assert ats.total == 2600
    # remote share = 1300 / 2600
    assert ats.remote_pct == 50.0
    # top metros exclude Remote/Various/Other
    assert [m.metro for m in ats.top_metros] == ["SF Bay Area", "New York"]
    assert ats.top_metros[0].n_roles == 900


def test_geo_report_sorted_by_total() -> None:
    report = geo_report(_FakeSession(ROWS))
    assert [g.source for g in report] == ["ats", "usajobs"]  # 2600 > 1855


def test_geo_report_empty_and_format() -> None:
    assert geo_report(_FakeSession([])) == []
    assert "no data" in format_geo([])
    assert "SF Bay Area" in format_geo(geo_report(_FakeSession(ROWS)))
