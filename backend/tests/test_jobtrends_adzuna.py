from __future__ import annotations

from app.jobtrends.adzuna import AdzunaClient, adzuna_snapshot, parse_adzuna


def _payload() -> dict:
    return {
        "results": [
            {
                "id": "123",
                "title": "Operations <strong>Coordinator</strong>",
                "description": "You will manually reconcile invoices and update spreadsheets daily.",
                "company": {"display_name": "Acme Co"},
                "location": {"display_name": "Denver, CO"},
                "category": {"label": "Admin Jobs"},
                "redirect_url": "https://adzuna/job/123",
                "created": "2026-07-01T00:00:00Z",
                "salary_min": 55000,
                "salary_max": 65000,
            },
            {"id": "124", "title": "", "description": "no title, skipped"},
        ]
    }


def test_parse_adzuna_maps_fields_and_strips_html() -> None:
    jobs = parse_adzuna(_payload(), country="us")
    assert len(jobs) == 1  # the empty-title row is skipped
    j = jobs[0]
    assert j.source == "adzuna" and j.provider == "adzuna"
    assert j.title == "Operations Coordinator"  # html stripped
    assert j.company_name == "Acme Co"
    assert j.location == "Denver, CO"
    assert j.comp_min == 55000 and j.comp_max == 65000
    assert j.comp_currency == "USD" and j.comp_kind == "structured"
    assert j.id == "adzuna:acme-co:123" or j.id.endswith(":123")


def test_parse_adzuna_no_salary_omits_comp() -> None:
    payload = {
        "results": [
            {
                "id": "9",
                "title": "Clerk",
                "description": "filing",
                "company": {"display_name": "X"},
            }
        ]
    }
    j = parse_adzuna(payload)[0]
    assert j.comp_min is None and j.comp_kind is None


def test_client_not_configured_is_skipped() -> None:
    client = AdzunaClient(app_id="", app_key="")
    assert client.configured is False


def test_snapshot_skips_when_unconfigured() -> None:
    # No DB touched when unconfigured — returns skipped without needing a session.
    result = adzuna_snapshot(session=None, client=AdzunaClient(app_id="", app_key=""))
    assert result["skipped"] == 1
