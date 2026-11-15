from datetime import UTC, datetime

import httpx

from app.jobtrends.himalayas import (
    PROVIDER_HIMALAYAS,
    SOURCE_HIMALAYAS,
    HimalayasClient,
    parse_himalayas,
)

JOB = {
    "title": "Senior Software Engineer",
    "companyName": "Acme",
    "companySlug": "acme",
    "minSalary": 150000,
    "maxSalary": 190000,
    "salaryPeriod": "annual",
    "currency": "USD",
    "locationRestrictions": ["United States"],
    "parentCategories": ["Developer"],
    "description": "<p>Build &amp; ship Python.</p>",
    "pubDate": "1794317072",
    "expiryDate": 1796908875,
    "applicationLink": "https://himalayas.app/companies/acme/jobs/senior-software-engineer?utm_source=api",
    "guid": "https://himalayas.app/companies/acme/jobs/senior-software-engineer",
}


def test_parse_himalayas_maps_fields() -> None:
    [j] = parse_himalayas({"jobs": [JOB]})
    assert j.source == SOURCE_HIMALAYAS
    assert j.provider == PROVIDER_HIMALAYAS
    assert j.id == "himalayas:acme:acme/jobs/senior-software-engineer"
    assert j.url == JOB["applicationLink"]
    assert j.location == "Remote (United States)"
    assert j.department == "Developer"
    assert j.content_text == "Build & ship Python."
    assert j.posted_at == datetime.fromtimestamp(1794317072, tz=UTC)
    assert j.expires_at == datetime.fromtimestamp(1796908875, tz=UTC)
    assert (j.comp_min, j.comp_max, j.comp_kind) == (150000, 190000, "structured")


def test_parse_himalayas_monthly_pay_and_long_location_list() -> None:
    job = {
        **JOB,
        "salaryPeriod": "monthly",
        "minSalary": "10000",
        "maxSalary": "15000",
        "locationRestrictions": ["Canada", "United States", "Mexico", "Brazil"],
    }
    [j] = parse_himalayas({"jobs": [job]})
    assert (j.comp_min, j.comp_max) == (120000, 180000)
    assert j.location == "Remote (United States + 3 more)"


def test_parse_himalayas_skips_unusable_rows_and_bad_pay() -> None:
    rows = [
        {**JOB, "guid": "https://example.com/x"},
        {**JOB, "title": ""},
        {
            **JOB,
            "minSalary": None,
            "maxSalary": None,
            "currency": "EUR",
            "locationRestrictions": [],
        },
        "junk",
    ]
    [j] = parse_himalayas({"jobs": rows})
    assert j.comp_max is None
    assert j.location == "Remote (Worldwide)"


def test_client_sends_us_senior_recent_search() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"jobs": [JOB]})

    client = HimalayasClient(
        transport=httpx.MockTransport(handler), sleep=lambda _s: None
    )
    jobs = client.search("software engineer", 2)
    assert len(jobs) == 1
    params = seen[0].url.params
    assert params["country"] == "US"
    assert params["seniority"] == "Senior"
    assert params["sort"] == "recent"
    assert params["page"] == "2"
    assert params["q"] == "software engineer"
