"""Unit tests for the USAJobs connector — pure parse + mock-transport client.

Live verification needs a (free) API key, so the DB snapshot path is checked
against the real API in the integration/deploy step; here we cover parsing +
wiring against a realistic sample payload, DB-free.
"""

import httpx

from app.jobtrends.ats import SOURCE_USAJOBS
from app.jobtrends.usajobs import PROVIDER_USAJOBS, UsaJobsClient, parse_usajobs

# Trimmed but shape-accurate USAJobs /api/search response.
PAYLOAD = {
    "SearchResult": {
        "SearchResultCount": 2,
        "SearchResultCountAll": 18432,
        "SearchResultItems": [
            {
                "MatchedObjectId": "830000001",
                "MatchedObjectDescriptor": {
                    "PositionID": "IT-2210-1",
                    "PositionTitle": "IT Specialist (INFOSEC)",
                    "PositionURI": "https://www.usajobs.gov/job/830000001",
                    "OrganizationName": "Cybersecurity and Infrastructure Security Agency",
                    "DepartmentName": "Department of Homeland Security",
                    "PositionLocationDisplay": "Arlington, Virginia",
                    "JobCategory": [{"Name": "Information Technology", "Code": "2210"}],
                    "PositionRemuneration": [
                        {
                            "MinimumRange": "99000",
                            "MaximumRange": "153000",
                            "RateIntervalCode": "PA",
                        }
                    ],
                    "PublicationStartDate": "2026-07-01T00:00:00.0000000",
                    "UserArea": {
                        "Details": {
                            "JobSummary": "<p>Defend federal <b>networks</b>.</p>"
                        }
                    },
                },
            },
            {
                "MatchedObjectId": "830000002",
                "MatchedObjectDescriptor": {
                    "PositionTitle": "Nurse",
                    "OrganizationName": "Veterans Health Administration",
                    "DepartmentName": "Department of Veterans Affairs",
                    "PositionLocationDisplay": "Multiple Locations",
                    "JobCategory": [{"Name": "Medical", "Code": "0610"}],
                    "PublicationStartDate": "2026-06-20T00:00:00.0000000",
                },
            },
        ],
    }
}


def test_parse_usajobs_shape_and_total() -> None:
    jobs, total = parse_usajobs(PAYLOAD)
    assert total == 18432
    assert len(jobs) == 2
    j = jobs[0]
    assert j.source == SOURCE_USAJOBS
    assert j.provider == PROVIDER_USAJOBS
    assert j.external_id == "830000001"
    assert j.id == "usajobs:cybersecurity-and-infrastructure-security-agency:830000001"
    assert j.company_name == "Cybersecurity and Infrastructure Security Agency"
    assert j.title == "IT Specialist (INFOSEC)"
    assert j.department == "Information Technology"  # job category -> department
    assert j.location == "Arlington, Virginia"
    assert "Defend federal networks" in j.content_text  # html stripped


def test_parse_usajobs_second_item_minimal() -> None:
    jobs, _ = parse_usajobs(PAYLOAD)
    j = jobs[1]
    assert j.external_id == "830000002"  # falls back to MatchedObjectId
    assert j.company_name == "Veterans Health Administration"
    assert j.department == "Medical"
    assert j.content_text == ""  # no JobSummary


def test_parse_usajobs_empty() -> None:
    assert parse_usajobs({}) == ([], 0)


def test_client_not_configured_without_key() -> None:
    assert UsaJobsClient(api_key="", user_agent="").configured is False
    assert UsaJobsClient(api_key="k", user_agent="me@example.com").configured is True


def test_client_sends_auth_headers() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        assert request.url.path == "/api/search"
        return httpx.Response(200, json=PAYLOAD)

    client = UsaJobsClient(
        api_key="secret-key",
        user_agent="me@example.com",
        transport=httpx.MockTransport(handler),
    )
    payload = client.search(page=1, results_per_page=50)
    assert payload["SearchResult"]["SearchResultCountAll"] == 18432
    assert seen["authorization-key"] == "secret-key"
    assert seen["user-agent"] == "me@example.com"
