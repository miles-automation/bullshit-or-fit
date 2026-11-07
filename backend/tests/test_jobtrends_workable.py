import json

import httpx
import pytest

from app.jobtrends.workable import (
    PROVIDER_WORKABLE,
    WorkableClient,
    WorkableConfig,
    parse_workable,
)

CFG = WorkableConfig(account="acme")


def _job(code: str, *, remote: bool = False) -> dict[str, object]:
    return {
        "shortcode": code,
        "title": f"Developer {code}",
        "remote": remote,
        "location": {"city": "Niwot", "region": "Colorado", "country": "United States"},
        "published": "2026-09-21T00:00:00.000Z",
        "department": ["Engineering"],
    }


def test_parse_maps_fields_and_remote_location() -> None:
    onsite, remote = parse_workable(
        "acme", "Acme", CFG, [_job("A1"), _job("B2", remote=True)]
    )
    assert onsite.provider == PROVIDER_WORKABLE
    assert onsite.location == "Niwot, Colorado, United States"
    assert onsite.url == "https://apply.workable.com/acme/j/A1/"
    assert onsite.department == "Engineering"
    assert onsite.posted_at is not None and onsite.posted_at.year == 2026
    assert remote.location == "Remote (Niwot, Colorado, United States)"


@pytest.mark.parametrize("results", [None, [None], [{"title": "Dev"}]])
def test_parse_raises_on_malformed_results(results: object) -> None:
    with pytest.raises(ValueError):
        parse_workable("acme", "Acme", CFG, results)


def _client(pages: list[dict[str, object]], bodies: list[object]) -> WorkableClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json=pages[len(bodies) - 1])

    return WorkableClient(transport=httpx.MockTransport(handler))


def test_client_follows_next_page_token() -> None:
    bodies: list[object] = []
    client = _client(
        [
            {"total": 3, "results": [_job("A"), _job("B")], "nextPage": "tok"},
            {"results": [_job("C")]},
        ],
        bodies,
    )
    assert len(client.fetch("acme", "Acme", CFG)) == 3
    assert bodies == [{}, {"token": "tok"}]


def test_client_accepts_an_empty_account() -> None:
    client = _client([{"total": 0, "results": []}], [])
    assert client.fetch("acme", "Acme", CFG) == []


@pytest.mark.parametrize(
    "page",
    [
        {"results": []},
        {"total": -1, "results": []},
        {"total": True, "results": []},
        {"total": 5, "results": [_job("A")]},
        {"total": 1, "error": "x"},
    ],
)
def test_client_raises_on_incomplete_or_invalid_response(
    page: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _client([page], []).fetch("acme", "Acme", CFG)
