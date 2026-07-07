"""Unit tests for the remote-board connectors (Remotive + RemoteOK)."""

from datetime import datetime

import httpx

from app.jobtrends.ats import SOURCE_REMOTE
from app.jobtrends.remote_boards import (
    PROVIDER_REMOTEOK,
    PROVIDER_REMOTIVE,
    RemoteClient,
    parse_remoteok,
    parse_remotive,
)

REMOTIVE_PAYLOAD = {
    "jobs": [
        {
            "id": 111,
            "title": "Senior Backend Engineer",
            "company_name": "Acme Co",
            "category": "Software Development",
            "candidate_required_location": "Worldwide",
            "url": "https://remotive.com/remote-jobs/x/111",
            "description": "<p>Python &amp; Postgres.</p>",
            "publication_date": "2026-07-04T16:53:04",
        }
    ]
}

REMOTEOK_PAYLOAD = [
    {"legal": "terms", "last_updated": "123"},  # first element is a notice, not a job
    {
        "id": "999",
        "position": "Staff Engineer",
        "company": "Globex",
        "location": "Remote",
        "url": "https://remoteok.com/remote-jobs/999",
        "description": "<b>Go</b> role",
        "date": "2026-07-06T15:08:08+00:00",
    },
]


def test_parse_remotive() -> None:
    jobs = parse_remotive(REMOTIVE_PAYLOAD)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.source == SOURCE_REMOTE
    assert j.provider == PROVIDER_REMOTIVE
    assert j.id == "remotive:acme-co:111"  # company slugified into the token
    assert j.company_name == "Acme Co"
    assert j.department == "Software Development"  # category -> department
    assert j.location == "Worldwide"
    assert "Python & Postgres." in j.content_text
    assert j.posted_at == datetime.fromisoformat("2026-07-04T16:53:04")


def test_parse_remoteok_skips_legal_notice() -> None:
    jobs = parse_remoteok(REMOTEOK_PAYLOAD)
    assert len(jobs) == 1  # the legal element is skipped
    j = jobs[0]
    assert j.provider == PROVIDER_REMOTEOK
    assert j.id == "remoteok:globex:999"
    assert j.title == "Staff Engineer"
    assert j.location == "Remote"
    assert j.department is None
    assert "Go role" in j.content_text


def test_remote_client_remotive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "remotive.com" in str(request.url)
        return httpx.Response(200, json=REMOTIVE_PAYLOAD)

    jobs = RemoteClient(transport=httpx.MockTransport(handler)).fetch_remotive()
    assert jobs[0].external_id == "111"


def test_remote_client_remoteok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "remoteok.com" in str(request.url)
        return httpx.Response(200, json=REMOTEOK_PAYLOAD)

    jobs = RemoteClient(transport=httpx.MockTransport(handler)).fetch_remoteok()
    assert [j.external_id for j in jobs] == ["999"]
