"""Unit tests for the OEWS location-wage source (pure parse + fake-session reader)."""

import httpx

from app.jobtrends.models import OewsWage
from app.jobtrends.oews import (
    STATES,
    OewsClient,
    available_areas,
    parse_onet_annual,
    wage_bands,
)

# O*NET embeds an hourly AND an annual chart; the parser must pick the annual one.
FIXTURE = """
<script>
$(function(){createWagesChart({"containerId":"hourlyChartContainer",
  "dataNational":[{"percent":"50","x":"65"}],
  "dataState":[{"percent":"50","x":"55"}]});});
$(function(){createWagesChart({"containerId":"annualChartContainer",
  "dataNational":[{"percent":"10","x":"82460"},{"percent":"25","x":"105210"},
    {"percent":"50","x":"135980"},{"percent":"75","x":"171980"},{"percent":"90","x":"214670"}],
  "dataState":[{"percent":"10","x":"72950"},{"percent":"25","x":"100560"},
    {"percent":"50","x":"115170"},{"percent":"75","x":"153090"},{"percent":"90","x":"156620"}]});});
</script>
"""


def test_parse_onet_annual_picks_annual_not_hourly() -> None:
    r = parse_onet_annual(FIXTURE)
    assert r["national"][50] == 135980  # annual median, not the hourly 65
    assert r["state"] == {10: 72950, 25: 100560, 50: 115170, 75: 153090, 90: 156620}


def test_parse_onet_annual_empty_when_no_chart() -> None:
    assert parse_onet_annual("<html>no chart here</html>") == {}


def test_states_cover_50_plus_dc() -> None:
    assert len(STATES) == 51
    assert STATES["WY"] == "Wyoming" and "DC" in STATES


def test_client_fetches_localwages_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "onetonline.org" in request.url.host
        assert "15-1252.00" in request.url.path
        assert dict(request.url.params)["st"] == "WY"
        return httpx.Response(200, text=FIXTURE)

    text = OewsClient(transport=httpx.MockTransport(handler)).fetch("15-1252", "WY")
    assert "annualChartContainer" in text


# ---- reader (fake session over model instances) ---------------------------


class _Scalars:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> list:
        return self._rows

    def all(self) -> list:
        return [(r.area_code, r.area_name) for r in self._rows]


class _FakeSession:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def execute(self, *_a, **_k) -> _Scalars:  # noqa: ANN002, ANN003
        return _Scalars(self._rows)


def _mk(code: str, name: str, atype: str, med: int) -> OewsWage:
    return OewsWage(
        soc="15-1252",
        occupation="Software Developers",
        area_type=atype,
        area_code=code,
        area_name=name,
        p10_usd=med - 40000,
        p25_usd=med - 15000,
        median_usd=med,
        p75_usd=med + 30000,
        p90_usd=med + 60000,
    )


def test_wage_bands_returns_area_plus_national() -> None:
    rows = [
        _mk("US", "United States", "national", 135980),
        _mk("WY", "Wyoming", "state", 115170),
    ]
    r = wage_bands(_FakeSession(rows), "wy")  # case-insensitive
    assert r.occupation == "Software Developers"
    assert r.area is not None and r.area.median_usd == 115170
    assert r.national is not None and r.national.median_usd == 135980


def test_available_areas_lists_states() -> None:
    rows = [
        _mk("CA", "California", "state", 174000),
        _mk("WY", "Wyoming", "state", 115170),
    ]
    out = available_areas(_FakeSession(rows))
    assert {"code": "CA", "name": "California"} in out
