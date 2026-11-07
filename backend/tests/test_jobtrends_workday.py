"""Unit tests for the Workday CXS source (pure parse + paginating client)."""

import httpx
import pytest

from app.jobtrends.workday import (
    PROVIDER_WORKDAY,
    WorkdayClient,
    WorkdayConfig,
    parse_workday,
)

CFG = WorkdayConfig(tenant="hpe", host="wd5", site="Jobsathpe", search="Fort Collins")


def _page(paths: list[str], total: int) -> dict:
    return {
        "total": total,
        "jobPostings": [
            {"title": f"Role {i}", "externalPath": p, "locationsText": "2 Locations"}
            for i, p in enumerate(paths)
        ],
    }


def test_parse_workday_location_from_path_and_full_url() -> None:
    payload = _page(["/job/Fort-Collins-Colorado/Principal-Linux-Kernel-Dev_123"], 1)
    jobs = parse_workday("hpe-fort-collins", "HPE", CFG, payload)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.provider == PROVIDER_WORKDAY
    assert j.company_token == "hpe-fort-collins"
    # location parsed from the externalPath slug (hyphens -> spaces)
    assert j.location == "Fort Collins Colorado"
    # stable id key = the externalPath; full URL joins the site base
    assert j.external_id == "/job/Fort-Collins-Colorado/Principal-Linux-Kernel-Dev_123"
    assert j.url == (
        "https://hpe.wd5.myworkdayjobs.com/en-US/Jobsathpe"
        "/job/Fort-Collins-Colorado/Principal-Linux-Kernel-Dev_123"
    )
    assert j.posted_at is None and j.comp_min is None


def test_parse_workday_skips_pathless_rows() -> None:
    payload = {"jobPostings": [{"title": "No path", "externalPath": ""}]}
    assert parse_workday("t", "T", CFG, payload) == []


def test_broadcom_style_path_still_contains_tokens() -> None:
    # Broadcom's slug format differs (USA-Colorado-Fort-Collins-…) but still yields a
    # location string the caller's role_in_shed can match.
    payload = _page(["/job/USA-Colorado-Fort-Collins-4380/IC-Design-Engineer_9"], 1)
    loc = parse_workday("b", "Broadcom", CFG, payload)[0].location
    assert loc is not None and "fort collins" in loc.lower()


def test_client_paginates_until_total_reached() -> None:
    seen_offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        off = body["offset"]
        seen_offsets.append(off)
        assert body["searchText"] == "Fort Collins"
        # 30 total → page 0 (0-19) full, page 1 (20-29) partial, then stop.
        if off == 0:
            return httpx.Response(
                200, json=_page([f"/job/Fort-Collins/R{i}_{i}" for i in range(20)], 30)
            )
        return httpx.Response(
            200, json=_page([f"/job/Fort-Collins/R{i}_{i}" for i in range(20, 30)], 30)
        )

    client = WorkdayClient(transport=httpx.MockTransport(handler))
    jobs = client.fetch("hpe-fort-collins", "HPE", CFG)
    assert len(jobs) == 30
    assert seen_offsets == [0, 20]  # stopped once (page+1)*20 >= total


@pytest.mark.parametrize(
    "payload", [{}, {"total": -1, "jobPostings": []}, {"total": False}]
)
def test_client_raises_when_first_page_total_is_invalid(
    payload: dict[str, object],
) -> None:
    client = WorkdayClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    with pytest.raises(ValueError):
        client.fetch("t", "T", CFG)


def test_client_accepts_a_genuinely_empty_board() -> None:
    client = WorkdayClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"total": 0, "jobPostings": []})
        )
    )
    assert client.fetch("t", "T", CFG) == []


def test_client_ignores_zero_total_on_later_pages() -> None:
    import json

    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        off = json.loads(request.content)["offset"]
        offsets.append(off)
        paths = [f"/job/Fort-Collins-CO/R{off + i}_{off + i}" for i in range(20)]
        return httpx.Response(200, json=_page(paths, 60 if off == 0 else 0))

    client = WorkdayClient(transport=httpx.MockTransport(handler))
    jobs = client.fetch("t", "T", CFG)
    assert offsets == [0, 20, 40]
    assert len(jobs) == 60


def test_client_raises_when_pages_repeat_short_of_total() -> None:
    same = _page([f"/job/Fort-Collins-CO/R{i}_{i}" for i in range(20)], 40)
    client = WorkdayClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=same))
    )
    with pytest.raises(ValueError):
        client.fetch("t", "T", CFG)


def test_client_raises_on_empty_page_before_total() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        off = json.loads(request.content)["offset"]
        paths = (
            [f"/job/Fort-Collins-CO/R{i}_{i}" for i in range(20)] if off == 0 else []
        )
        return httpx.Response(200, json=_page(paths, 60))

    client = WorkdayClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError):
        client.fetch("t", "T", CFG)


def test_client_raises_on_empty_first_page_with_positive_total() -> None:
    client = WorkdayClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"total": 94, "jobPostings": []})
        )
    )
    with pytest.raises(ValueError):
        client.fetch("t", "T", CFG)


def test_campus_city_tags_only_listed_buildings() -> None:
    cfg = WorkdayConfig(
        tenant="csusystem",
        host="wd12",
        site="fortcollins_careers",
        campus_city="Fort Collins",
        campus_locations=frozenset({"Sage Hall"}),
    )
    payload = _page(
        [
            "/job/Sage-Hall/IT-Support_1",
            "/job/Fort-Collins-CO/IT-Support_2",
            "/job/CSFS-Durango/GIS-Analyst_3",
            "/job/Mountain-Campus/IT-Support_4",
        ],
        4,
    )
    locations = [j.location for j in parse_workday("c", "C", cfg, payload)]
    assert locations == [
        "Sage Hall, Fort Collins",
        "Fort Collins CO",
        "CSFS Durango",
        "Mountain Campus",
    ]
