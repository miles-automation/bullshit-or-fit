import httpx
import pytest

from app.jobtrends.rippling import (
    PROVIDER_RIPPLING,
    RipplingClient,
    RipplingConfig,
    parse_rippling,
)

CFG = RipplingConfig(board="acme-careers")

JOB = {
    "uuid": "840d45c9",
    "name": "Software Engineer",
    "department": {"id": "Technology", "label": "Technology"},
    "url": "https://ats.rippling.com/acme-careers/jobs/840d45c9",
    "workLocation": {"label": "Loveland, CO", "id": "Loveland, CO"},
}


def test_parse_maps_fields() -> None:
    job = parse_rippling("acme", "Acme", CFG, [JOB])[0]
    assert job.provider == PROVIDER_RIPPLING
    assert job.external_id == "840d45c9"
    assert job.title == "Software Engineer"
    assert job.location == "Loveland, CO"
    assert job.department == "Technology"
    assert job.url == "https://ats.rippling.com/acme-careers/jobs/840d45c9"


def test_parse_accepts_an_empty_board() -> None:
    assert parse_rippling("acme", "Acme", CFG, []) == []


@pytest.mark.parametrize(
    "payload",
    [{"error": "not found"}, [None], [{"uuid": "", "name": "Dev"}], [{"uuid": "1"}]],
)
def test_parse_raises_on_malformed_payload(payload: object) -> None:
    with pytest.raises(ValueError):
        parse_rippling("acme", "Acme", CFG, payload)


def test_client_fetches_board() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/platform/api/ats/v1/board/acme-careers/jobs"
        assert "@" not in request.headers["user-agent"]
        return httpx.Response(200, json=[JOB])

    client = RipplingClient(transport=httpx.MockTransport(handler))
    assert len(client.fetch("acme", "Acme", CFG)) == 1


def test_client_raises_on_http_error() -> None:
    client = RipplingClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch("acme", "Acme", CFG)
