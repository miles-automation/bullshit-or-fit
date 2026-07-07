"""Unit tests for the USAJobs connector — pure parse + mock-transport client.

Live verification needs a (free) API key, so the DB snapshot path is checked
against the real API in the integration/deploy step; here we cover parsing +
wiring against a realistic sample payload, DB-free.
"""

import httpx

from app.jobtrends.ats import SOURCE_USAJOBS
from app.jobtrends.usajobs import (
    PROVIDER_USAJOBS,
    UsaJobsClient,
    parse_usajobs,
    usajobs_snapshot,
)

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


class _BlockedSession:
    """Fake session for the CDN-block path: usajobs_snapshot returns early before
    touching the DB except for the open-count read."""

    def scalar(self, *_a, **_k) -> int:  # noqa: ANN002, ANN003
        return 0

    def execute(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("must not write/close when the fetch is blocked")

    def commit(self) -> None:
        raise AssertionError("must not commit when the fetch is blocked")


def test_snapshot_degrades_on_cdn_block() -> None:
    # Simulate Akamai's edge 403 — the client raises, snapshot must not crash or
    # close existing rows.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Access Denied")

    client = UsaJobsClient(
        api_key="k", user_agent="me@example.com", transport=httpx.MockTransport(handler)
    )
    result = usajobs_snapshot(_BlockedSession(), client, max_pages=4)
    assert result == {"configured": 1, "blocked": 1, "open_roles": 0}


def test_snapshot_skips_without_key() -> None:
    result = usajobs_snapshot(
        _BlockedSession(), UsaJobsClient(api_key="", user_agent="")
    )
    assert result == {"configured": 0, "open_roles": 0}
