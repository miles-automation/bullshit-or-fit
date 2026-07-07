"""Unit tests for the ATS (Greenhouse) connector — pure parse + mock-transport client.

The DB snapshot/close-detection path is exercised against a real Postgres in the
integration step; here we cover parsing and HTTP wiring DB-free.
"""

from datetime import datetime

import httpx
import pytest

from app.jobtrends.ats import (
    PROVIDER_GREENHOUSE,
    AtsClient,
    AtsReport,
    Company,
    CompanyOpenings,
    format_ats_table,
    parse_greenhouse,
)

ACME = Company("Acme", PROVIDER_GREENHOUSE, "acme")

GH_PAYLOAD = {
    "jobs": [
        {
            "id": 123,
            "title": "Staff Engineer",
            "location": {"name": "Remote - US"},
            "departments": [{"name": "Engineering"}, {"name": "Platform"}],
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
            "content": "<p>Build things with &amp; Python.</p>",
            "updated_at": "2026-07-01T09:00:00-04:00",
        },
        {
            "id": 456,
            "title": "Recruiter",
            "location": None,
            "departments": [],
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/456",
            "content": "",
            "updated_at": None,
        },
    ]
}


def test_parse_greenhouse_shape() -> None:
    jobs = parse_greenhouse(ACME, GH_PAYLOAD)
    assert len(jobs) == 2
    j = jobs[0]
    assert j.external_id == "123"
    assert j.id == "greenhouse:acme:123"
    assert j.title == "Staff Engineer"
    assert j.location == "Remote - US"
    assert j.department == "Engineering"  # first department
    assert j.company_name == "Acme"
    assert "Build things with & Python." in j.content_text  # html stripped + unescaped
    assert j.posted_at == datetime.fromisoformat("2026-07-01T09:00:00-04:00")


def test_parse_greenhouse_tolerates_nulls() -> None:
    j = parse_greenhouse(ACME, GH_PAYLOAD)[1]
    assert j.external_id == "456"
    assert j.location is None
    assert j.department is None
    assert j.content_text == ""
    assert j.posted_at is None


def test_parse_greenhouse_empty() -> None:
    assert parse_greenhouse(ACME, {}) == []


def test_ats_client_fetches_and_parses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/boards/acme/jobs"
        assert dict(request.url.params)["content"] == "true"
        return httpx.Response(200, json=GH_PAYLOAD)

    jobs = AtsClient(transport=httpx.MockTransport(handler)).fetch_company(ACME)
    assert [j.external_id for j in jobs] == ["123", "456"]


def test_ats_client_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        AtsClient().fetch_company(Company("X", "lever", "x"))


def test_format_ats_table() -> None:
    report = AtsReport(
        total_open=42,
        companies=2,
        top=[
            CompanyOpenings("Stripe", "stripe", 30),
            CompanyOpenings("Acme", "acme", 12),
        ],
    )
    out = format_ats_table(report)
    assert "42 open roles across 2 companies" in out
    assert "Stripe" in out and "30" in out


def test_format_ats_table_empty() -> None:
    assert "no data" in format_ats_table(AtsReport(0, 0, []))
