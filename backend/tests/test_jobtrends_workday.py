"""Unit tests for the Workday CXS source (pure parse + paginating client)."""

import httpx

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
            200, json=_page([f"/job/Fort-Collins/R{i}_{i}" for i in range(10)], 30)
        )

    client = WorkdayClient(transport=httpx.MockTransport(handler))
    jobs = client.fetch("hpe-fort-collins", "HPE", CFG)
    assert len(jobs) == 30
    assert seen_offsets == [0, 20]  # stopped once (page+1)*20 >= total


def test_client_stops_on_empty_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        off = json.loads(request.content)["offset"]
        if off == 0:
            return httpx.Response(200, json=_page(["/job/Fort-Collins/A_1"], 999))
        return httpx.Response(200, json={"jobPostings": []})

    client = WorkdayClient(transport=httpx.MockTransport(handler))
    jobs = client.fetch("t", "T", CFG)
    assert len(jobs) == 1  # empty second page breaks the loop despite total=999
