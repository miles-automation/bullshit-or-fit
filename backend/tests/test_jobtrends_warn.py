"""Unit tests for the WARN Act supply source (pure parse + fake-session readers)."""

from datetime import date

import httpx

from app.jobtrends.warn import (
    WARN_SOURCES,
    WarnClient,
    parse_oregon,
    parse_texas,
    warn_months,
    warn_report,
)

TX_ROWS = [
    {
        "notice_date": "2026-06-23T00:00:00.000",
        "job_site_name": "JPMorgan Chase & Co.",
        "city_name": "Plano",
        "total_layoff_number": "244",
        "layoff_date": "2026-08-22T00:00:00.000",
    },
    {"job_site_name": "", "total_layoff_number": "10"},  # no company -> skipped
]

OR_ROWS = [
    {
        "warn": "9507",
        "company_name": "Coburg Facility",
        "city": "Coburg",
        "state": "OR",
        "layoff_date": "2026-07-17T00:00:00.000",
        "laid_off": "83",
        "layoff_type": "Permanent closure",
        "received_date": "2026-05-13T00:00:00.000",
    }
]


def test_parse_texas() -> None:
    out = parse_texas(TX_ROWS)
    assert len(out) == 1  # empty-company row skipped
    n = out[0]
    assert n.state == "TX"
    assert n.company == "JPMorgan Chase & Co."
    assert n.city == "Plano"
    assert n.employees_affected == 244
    assert n.notice_date == date(2026, 6, 23)
    assert n.effective_date == date(2026, 8, 22)
    # composite id (Texas has no notice number) is deterministic
    assert n.id == parse_texas(TX_ROWS)[0].id
    assert n.id.startswith("TX:2026-06-23:")


def test_texas_distinct_sites_same_day_get_distinct_ids() -> None:
    # Same company + notice date but different sites must not collapse to one id.
    rows = [
        {
            "notice_date": "2026-06-23T00:00:00.000",
            "job_site_name": "Acme",
            "city_name": "Austin",
            "total_layoff_number": "50",
            "layoff_date": "2026-08-01T00:00:00.000",
        },
        {
            "notice_date": "2026-06-23T00:00:00.000",
            "job_site_name": "Acme",
            "city_name": "Dallas",
            "total_layoff_number": "70",
            "layoff_date": "2026-08-01T00:00:00.000",
        },
    ]
    ids = [n.id for n in parse_texas(rows)]
    assert len(set(ids)) == 2


def test_texas_true_duplicate_rows_share_id() -> None:
    # A feed that lists the identical notice twice -> same id (deduped at upsert).
    row = {
        "notice_date": "2026-06-23T00:00:00.000",
        "job_site_name": "Acme",
        "city_name": "Austin",
        "total_layoff_number": "50",
        "layoff_date": "2026-08-01T00:00:00.000",
    }
    ids = [n.id for n in parse_texas([row, dict(row)])]
    assert ids[0] == ids[1]


def test_parse_oregon_fields() -> None:
    n = parse_oregon(OR_ROWS)[0]
    assert n.state == "OR"  # attributed to the source database, not the row field
    assert n.company == "Coburg Facility"
    assert n.employees_affected == 83
    assert n.notice_date == date(2026, 5, 13)
    assert n.layoff_type == "Permanent closure"
    assert n.id.startswith("OR:9507:")  # warn# + row discriminators
    assert parse_oregon(OR_ROWS)[0].id == n.id  # deterministic


def test_oregon_multi_site_same_warn_stays_distinct() -> None:
    # One filing (same warn#) split across sites with different counts must not
    # collapse — that would drop employees. Distinct rows -> distinct ids.
    rows = [
        {
            "warn": "9389",
            "company_name": "Century Blvd., Hillsboro",
            "city": "Hillsboro",
            "laid_off": "510",
            "received_date": "2026-03-01T00:00:00.000",
        },
        {
            "warn": "9389",
            "company_name": "25th Ave., Hillsboro",
            "city": "Hillsboro",
            "laid_off": "76",
            "received_date": "2026-03-01T00:00:00.000",
        },
    ]
    out = parse_oregon(rows)
    assert len({n.id for n in out}) == 2
    assert sum(n.employees_affected or 0 for n in out) == 586


def test_parse_handles_bad_values() -> None:
    rows = [
        {"job_site_name": "X", "total_layoff_number": "n/a", "notice_date": "bogus"}
    ]
    n = parse_texas(rows)[0]
    assert n.employees_affected is None
    assert n.notice_date is None


def test_warn_client_fetches_and_parses() -> None:
    tx = next(s for s in WARN_SOURCES if s.state == "TX")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "data.texas.gov" in request.url.host
        assert dict(request.url.params)["$order"].startswith("notice_date")
        return httpx.Response(200, json=TX_ROWS)

    out = WarnClient(transport=httpx.MockTransport(handler)).fetch(tx)
    assert [n.company for n in out] == ["JPMorgan Chase & Co."]


# ---- readers (fake session) -----------------------------------------------


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows

    def yield_per(self, _n: int) -> list:
        return self._rows


class _QueueSession:
    """Serves a queued result per execute() call (readers issue several)."""

    def __init__(self, *result_sets: list) -> None:
        self._q = list(result_sets)

    def execute(self, *_a, **_k) -> _Result:  # noqa: ANN002, ANN003
        return _Result(self._q.pop(0) if self._q else [])


def test_warn_months_series() -> None:
    rows = [(date(2026, 5, 1), 3, 500), (date(2026, 6, 1), 5, 1200)]
    out = warn_months(_QueueSession(rows))
    assert [(m.month, m.notices, m.employees_affected) for m in out] == [
        ("2026-05", 3, 500),
        ("2026-06", 5, 1200),
    ]


def test_warn_report_totals_and_recent() -> None:
    by_state = [("TX", 10, 2500), ("OR", 4, 600)]
    recent = [("JPMorgan Chase & Co.", "TX", "Plano", 244, date(2026, 6, 23))]
    report = warn_report(_QueueSession(by_state, recent))
    assert report.total_notices == 14
    assert report.total_employees == 3100
    assert report.states == ["OR", "TX"]
    assert report.by_state[0].state == "TX"  # sorted by employees desc
    assert report.recent[0].company == "JPMorgan Chase & Co."
    assert report.recent[0].notice_date == "2026-06-23"
