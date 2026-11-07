from urllib.parse import unquote

import httpx
import pytest

from app.jobtrends.oracle_hcm import (
    PROVIDER_ORACLE_HCM,
    OracleHcmClient,
    OracleHcmConfig,
    parse_oracle_hcm,
)

CFG = OracleHcmConfig(host="uw.example.oraclecloud.com", site="CX_1")


def _req(
    job_id: str, location: str = "Laramie, WY, United States"
) -> dict[str, object]:
    return {
        "Id": job_id,
        "Title": f"Systems Administrator {job_id}",
        "PrimaryLocation": location,
        "PostedDate": "2026-11-06",
        "ShortDescriptionStr": "Runs the cluster",
        "Department": None,
    }


def test_parse_maps_fields_and_builds_candidate_url() -> None:
    job = parse_oracle_hcm("uw", "UW", CFG, [_req("262752")])[0]
    assert job.provider == PROVIDER_ORACLE_HCM
    assert job.company_token == "uw"
    assert job.external_id == "262752"
    assert job.location == "Laramie, WY, United States"
    assert job.url == (
        "https://uw.example.oraclecloud.com/hcmUI/CandidateExperience/en/sites/"
        "CX_1/job/262752"
    )
    assert job.content_text == "Systems Administrator 262752 · Runs the cluster"
    assert job.posted_at is not None and job.posted_at.isoformat().startswith(
        "2026-11-06"
    )


def test_parse_skips_rows_without_id_or_title_and_tolerates_bad_date() -> None:
    rows: list[dict[str, object]] = [
        {"Id": "", "Title": "No id"},
        {"Id": "1", "Title": ""},
        {"Id": "2", "Title": "Developer", "PostedDate": "soon"},
    ]
    jobs = parse_oracle_hcm("uw", "UW", CFG, rows)
    assert [j.external_id for j in jobs] == ["2"]
    assert jobs[0].posted_at is None


def test_client_paginates_to_total() -> None:
    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = unquote(str(request.url))
        assert "finder=findReqs;siteNumber=CX_1,limit=50," in url
        assert "@" not in request.headers["user-agent"]
        offset = url.split("offset=")[1].split(",")[0]
        offsets.append(offset)
        rows = (
            [_req(str(i)) for i in range(50)]
            if offset == "0"
            else [_req("50"), _req("51")]
        )
        return httpx.Response(
            200, json={"items": [{"TotalJobsCount": 52, "requisitionList": rows}]}
        )

    client = OracleHcmClient(transport=httpx.MockTransport(handler))
    jobs = client.fetch("uw", "UW", CFG)
    assert offsets == ["0", "50"]
    assert len(jobs) == 52


def test_client_raises_when_response_has_no_items() -> None:
    client = OracleHcmClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": []}))
    )
    with pytest.raises(ValueError):
        client.fetch("uw", "UW", CFG)


def test_client_raises_on_http_error() -> None:
    client = OracleHcmClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503))
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch("uw", "UW", CFG)


def _oracle_response(payload: dict[str, object]) -> OracleHcmClient:
    return OracleHcmClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"items": [payload]})
        )
    )


def test_client_raises_when_requisition_list_is_missing() -> None:
    with pytest.raises(ValueError):
        _oracle_response({"TotalJobsCount": 230}).fetch("uw", "UW", CFG)


def test_client_raises_when_total_is_missing() -> None:
    with pytest.raises(ValueError):
        _oracle_response({"requisitionList": [_req("1")]}).fetch("uw", "UW", CFG)


def test_client_raises_on_empty_page_before_total() -> None:
    client = _oracle_response({"TotalJobsCount": 230, "requisitionList": []})
    with pytest.raises(ValueError):
        client.fetch("uw", "UW", CFG)


def test_client_accepts_a_genuinely_empty_board() -> None:
    client = _oracle_response({"TotalJobsCount": 0, "requisitionList": []})
    assert client.fetch("uw", "UW", CFG) == []


def test_client_keeps_first_page_total_when_later_page_reports_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        first = "offset=0," in unquote(str(request.url))
        payload = (
            {
                "TotalJobsCount": 230,
                "requisitionList": [_req(str(i)) for i in range(50)],
            }
            if first
            else {"TotalJobsCount": 0, "requisitionList": []}
        )
        return httpx.Response(200, json={"items": [payload]})

    client = OracleHcmClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError):
        client.fetch("uw", "UW", CFG)


@pytest.mark.parametrize("total", [-1, False])
def test_client_raises_on_invalid_total(total: object) -> None:
    client = _oracle_response({"TotalJobsCount": total, "requisitionList": []})
    with pytest.raises(ValueError):
        client.fetch("uw", "UW", CFG)
