"""Unit tests for the ATS (Greenhouse) connector — pure parse + mock-transport client.

The DB snapshot/close-detection path is exercised against a real Postgres in the
integration step; here we cover parsing and HTTP wiring DB-free.
"""

from datetime import datetime

import httpx
import pytest

from app.jobtrends.ats import (
    PROVIDER_ASHBY,
    PROVIDER_GREENHOUSE,
    PROVIDER_LEVER,
    AtsClient,
    AtsReport,
    Company,
    CompanyOpenings,
    format_ats_table,
    parse_ashby,
    parse_greenhouse,
    parse_lever,
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
        AtsClient().fetch_company(Company("X", "workday", "x"))


# ---- Lever -----------------------------------------------------------------

LEVER = Company("Mistral", PROVIDER_LEVER, "mistral")
LEVER_PAYLOAD = [
    {
        "id": "abc-1",
        "text": "Senior ML Engineer",
        "categories": {"location": "Paris", "team": "Research"},
        "hostedUrl": "https://jobs.lever.co/mistral/abc-1",
        "descriptionPlain": "Build models. Comp $180k-220k. Python, Rust.",
        "createdAt": 1753687796431,
    },
    {"text": "no id — skipped"},
]


def test_parse_lever_shape_and_freetext_comp() -> None:
    jobs = parse_lever(LEVER, LEVER_PAYLOAD)
    assert len(jobs) == 1  # the id-less row is skipped
    j = jobs[0]
    assert j.provider == PROVIDER_LEVER
    assert j.id == "lever:mistral:abc-1"
    assert j.title == "Senior ML Engineer"
    assert j.location == "Paris"
    assert j.department == "Research"
    assert j.posted_at is not None and j.posted_at.year == 2025
    # comp comes from the free-text parser (Lever salaryRange is rare)
    assert j.comp_kind == "parsed"
    assert (j.comp_min, j.comp_max) == (180000, 220000)


def test_parse_lever_reads_lists_and_additional() -> None:
    # Skills + pay commonly live in lists[]/additionalPlain, not descriptionPlain.
    payload = [
        {
            "id": "x1",
            "text": "Backend Engineer",
            "categories": {"location": "Remote"},
            "descriptionPlain": "Join our team building great things.",
            "lists": [
                {
                    "text": "Requirements",
                    "content": "<li>Deep Kubernetes experience</li>",
                }
            ],
            "additionalPlain": "The salary range is $190,000-$230,000 plus equity.",
        }
    ]
    j = parse_lever(LEVER, payload)[0]
    assert "Kubernetes" in j.content_text  # list content included
    assert "salary range" in j.content_text  # additional included
    # comp parsed from additionalPlain, which descriptionPlain alone would miss
    assert (j.comp_min, j.comp_max) == (190000, 230000)


# ---- Ashby -----------------------------------------------------------------

ASHBY = Company("Ramp", PROVIDER_ASHBY, "ramp")
ASHBY_PAYLOAD = {
    "jobs": [
        {
            "id": "j1",
            "title": "Backend Engineer",
            "location": "New York",
            "department": "Engineering",
            "jobUrl": "https://jobs.ashbyhq.com/ramp/j1",
            "descriptionPlain": "Ship product.",
            "publishedAt": "2026-07-01T00:00:00.000+00:00",
            "isListed": True,
            "compensation": {
                "compensationTierSummary": "$168K – $231K",
                "compensationTiers": [
                    {
                        "components": [
                            {
                                "compensationType": "Salary",
                                "interval": "1 YEAR",
                                "currencyCode": "USD",
                                "minValue": 168000,
                                "maxValue": 231000,
                            },
                            {"compensationType": "EquityCashValue", "minValue": None},
                        ]
                    }
                ],
            },
        },
        {
            "id": "j2",
            "title": "Unlisted role",
            "isListed": False,
        },
    ]
}


def test_parse_ashby_structured_comp() -> None:
    jobs = parse_ashby(ASHBY, ASHBY_PAYLOAD)
    assert len(jobs) == 1  # unlisted role skipped
    j = jobs[0]
    assert j.provider == PROVIDER_ASHBY
    assert j.id == "ashby:ramp:j1"
    assert j.location == "New York"
    assert j.department == "Engineering"
    # structured salary component -> annualized USD
    assert j.comp_kind == "structured"
    assert j.comp_currency == "USD"
    assert (j.comp_min, j.comp_max) == (168000, 231000)


def test_parse_ashby_falls_back_to_summary_when_no_structured() -> None:
    payload = {
        "jobs": [
            {
                "id": "k1",
                "title": "Designer",
                "descriptionPlain": "Make things pretty.",
                "compensation": {"compensationTierSummary": "$140k - $170k"},
            }
        ]
    }
    j = parse_ashby(ASHBY, payload)[0]
    assert j.comp_kind == "parsed"
    assert (j.comp_min, j.comp_max) == (140000, 170000)


def test_ats_client_dispatches_ashby() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "posting-api/job-board/ramp" in request.url.path
        return httpx.Response(200, json=ASHBY_PAYLOAD)

    jobs = AtsClient(transport=httpx.MockTransport(handler)).fetch_company(ASHBY)
    assert [j.external_id for j in jobs] == ["j1"]


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
