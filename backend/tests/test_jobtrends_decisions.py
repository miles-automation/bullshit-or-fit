from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.jobtrends.decisions import (
    SyncSummary,
    TrackerError,
    _identity,
    parse_tracker,
    sync_decisions,
)
from app.jobtrends.fit import (
    build_decided_index,
    posting_keys,
    fit_roles,
    format_fit_list,
    normalize_company,
    normalize_title,
    url_key,
)

TRACKER = """# Application tracker

Status: drafting, submitted, skipped.

| Company | Role | Link | Pay | Status | Notes |
|---|---|---|---|---|---|
| Ramp | Senior SWE, Product Foundations | [Ashby](https://jobs.ashbyhq.com/ramp/A9CB5FD3-1acb-474e-ab7c-b811eeb5b0c2/application) | $204k | submitted | |
| Reddit | Senior SWE, DevX | [Greenhouse](https://job-boards.greenhouse.io/reddit/jobs/7950520) | $190k | skipped | Go |
| Fredrin | Senior SWE | [Careers](https://www.fredrin.com/careers/senior-software-engineer/) | not listed | skipped | |
| NoLink | Staff Engineer | none | n/a | drafting | |

Unrelated paragraph.
"""


@pytest.mark.parametrize(
    ("url", "key"),
    [
        (
            "https://jobs.ashbyhq.com/ramp/a9cb5fd3-1acb-474e-ab7c-b811eeb5b0c2/application",
            "ashby:a9cb5fd3-1acb-474e-ab7c-b811eeb5b0c2",
        ),
        (
            "https://jobs.ashbyhq.com/Ramp/A9CB5FD3-1ACB-474E-AB7C-B811EEB5B0C2?src=x",
            "ashby:a9cb5fd3-1acb-474e-ab7c-b811eeb5b0c2",
        ),
        ("https://boards.greenhouse.io/reddit/jobs/7950520", "greenhouse:7950520"),
        (
            "https://job-boards.greenhouse.io/reddit/jobs/7950520?gh_src=abc",
            "greenhouse:7950520",
        ),
        ("https://www.reddit.com/careers/job?gh_jid=7950520", "greenhouse:7950520"),
        (
            "https://jobs.lever.co/acme/0b5e2a1c-1111-2222-3333-444455556666/apply",
            "lever:0b5e2a1c-1111-2222-3333-444455556666",
        ),
        (
            "https://www.fredrin.com/careers/senior-software-engineer/",
            "fredrin.com/careers/senior-software-engineer",
        ),
        (
            "https://boards.greenhouse.io/embed/job_app?for=acme&token=123",
            "greenhouse:123",
        ),
        (
            "https://boards.greenhouse.io/embed/job_app?for=other&token=456",
            "greenhouse:456",
        ),
        ("https://[invalid/jobs", None),
        (None, None),
        ("", None),
        ("none", None),
    ],
)
def test_url_key(url: str | None, key: str | None) -> None:
    assert url_key(url) == key


def test_parse_tracker_reads_every_decided_row() -> None:
    rows = parse_tracker(TRACKER)
    assert [(r.company, r.status) for r in rows] == [
        ("Ramp", "submitted"),
        ("Reddit", "skipped"),
        ("Fredrin", "skipped"),
        ("NoLink", "drafting"),
    ]
    assert rows[0].url == (
        "https://jobs.ashbyhq.com/ramp/A9CB5FD3-1acb-474e-ab7c-b811eeb5b0c2/application"
    )
    assert rows[1].title == "Senior SWE, DevX"
    assert rows[3].url is None


def test_parse_tracker_ignores_text_without_a_table() -> None:
    assert parse_tracker("# Tracker\n\nnothing yet\n") == []


def test_parse_tracker_rejects_a_truncated_row() -> None:
    text = TRACKER.replace(
        "| NoLink | Staff Engineer | none | n/a | drafting | |", "| NoLink | Staff"
    )
    with pytest.raises(TrackerError, match="cells"):
        parse_tracker(text)


def test_parse_tracker_rejects_a_table_without_required_columns() -> None:
    text = (
        TRACKER
        + "\n| Company | Title | Link | Status |\n|---|---|---|---|\n| A | B | none | skipped |\n"
    )
    with pytest.raises(TrackerError, match="role"):
        parse_tracker(text)


def test_parse_tracker_rejects_a_row_without_status() -> None:
    text = TRACKER.replace("| drafting |", "|  |")
    with pytest.raises(TrackerError, match="required"):
        parse_tracker(text)


def test_parse_tracker_handles_escaped_pipes_and_autolinks() -> None:
    text = (
        "| Company | Role | Link | Status |\n|---|---|---|---|\n"
        "| Acme | Senior SWE \\| Platform | <https://www.fredrin.com/careers/x/> | skipped |\n"
    )
    [row] = parse_tracker(text)
    assert row.title == "Senior SWE | Platform"
    assert row.url == "https://www.fredrin.com/careers/x/"
    assert url_key(row.url) == "fredrin.com/careers/x"


def _decision(url: str | None, company: str, title: str) -> tuple[str | None, str, str]:
    return (url_key(url), normalize_company(company), normalize_title(title))


RAMP = "https://jobs.ashbyhq.com/ramp/a9cb5fd3-1acb-474e-ab7c-b811eeb5b0c2"
OTHER_ASHBY = "https://jobs.ashbyhq.com/acme/0b5e2a1c-1111-2222-3333-444455556666"


def test_decided_index_matches_by_link() -> None:
    index = build_decided_index([_decision(RAMP, "Ramp", "Senior SWE")], [])
    assert index.covers(
        "Ramp Business Corp",
        "Senior Software Engineer, Product Foundations",
        posting_keys(RAMP + "/application"),
    )


def test_decided_index_matches_a_copy_through_the_stored_posting() -> None:
    index = build_decided_index(
        [_decision(RAMP, "Ramp", "Senior SWE, Product Foundations")],
        [
            (
                "Ramp",
                "Senior Software Engineer | Product Foundations",
                posting_keys(RAMP),
            )
        ],
    )
    assert index.covers(
        "Ramp",
        "Senior Software Engineer | Product Foundations",
        posting_keys(
            "https://himalayas.app/companies/ramp/jobs/senior-software-engineer"
        ),
    )


def test_decided_index_matches_same_role_from_another_source() -> None:
    index = build_decided_index(
        [
            _decision(
                "https://datagrail.io/careers/staff",
                "DataGrail",
                "Staff Software Engineer",
            )
        ],
        [],
    )
    assert index.covers(
        "DataGrail, Inc.",
        "Staff Software Engineer",
        posting_keys(
            "https://himalayas.app/companies/datagrail/jobs/staff-software-engineer"
        ),
    )


def test_decided_index_keeps_a_distinct_requisition_with_the_same_title() -> None:
    index = build_decided_index(
        [_decision(RAMP, "Acme", "Senior Software Engineer")],
        [("Acme", "Senior Software Engineer", posting_keys(RAMP))],
    )
    assert not index.covers(
        "Acme", "Senior Software Engineer", posting_keys(OTHER_ASHBY)
    )
    assert index.covers(
        "Acme",
        "Senior Software Engineer",
        posting_keys("https://himalayas.app/companies/acme/jobs/sse"),
    )


def test_decided_index_keeps_a_different_level_at_the_same_company() -> None:
    index = build_decided_index(
        [_decision("https://x.test/1", "Twilio", "Staff Software Engineer")], []
    )
    assert not index.covers(
        "Twilio", "Senior Software Engineer", posting_keys("https://x.test/2")
    )
    assert not index.covers("Twilio", "Principal Software Engineer", posting_keys(None))


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, *results: list[Any]) -> None:
        self._results = list(results)

    def execute(self, _statement: object) -> _Result:
        return _Result(self._results.pop(0))


NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)


def _job(**kw: Any) -> SimpleNamespace:  # noqa: ANN401
    base: dict[str, Any] = dict(
        id="ats:acme:1",
        source="ats",
        company_name="Acme",
        title="Senior Software Engineer",
        location="Remote - US",
        url="https://jobs.ashbyhq.com/acme/a9cb5fd3-1acb-474e-ab7c-b811eeb5b0c2",
        comp_min=None,
        comp_max=200_000,
        comp_currency="USD",
        first_seen=NOW,
        posted_at=None,
        content_text="Python and TypeScript.",
        provider=None,
        external_id=None,
        team_size=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _posting(job: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        company_name=job.company_name,
        title=job.title,
        url=job.url,
        provider=job.provider,
        external_id=job.external_id,
        posted_at=job.posted_at,
    )


def _stored(url: str | None, company: str, title: str) -> SimpleNamespace:
    key, company_key, title_key = _decision(url, company, title)
    return SimpleNamespace(
        url_key=key, company_key=company_key, title_key=title_key, status="skipped"
    )


def test_fit_roles_hides_decided_roles_and_their_copies() -> None:
    ats = _job()
    copy = _job(
        source="himalayas",
        location="Remote - United States",
        url="https://himalayas.app/companies/acme/jobs/senior-software-engineer",
    )
    other = _job(title="Staff Software Engineer", url=OTHER_ASHBY)
    session = _Session(
        [ats, copy, other],
        [_posting(ats), _posting(other)],
        [_stored(ats.url, "Acme", "Senior SWE")],
    )
    result = fit_roles(session, now=NOW)  # type: ignore[arg-type]
    assert [r.title for r in result.roles] == ["Staff Software Engineer"]
    assert result.hidden == 1


def test_fit_roles_hides_a_whole_group_when_one_copy_matches() -> None:
    copy = _job(
        source="himalayas",
        url="https://himalayas.app/companies/acme/jobs/senior-software-engineer",
    )
    ats = _job()
    session = _Session(
        [copy, ats],
        [_posting(ats)],
        [_stored(ats.url, "Ramp", "Unrelated title")],
    )
    result = fit_roles(session, now=NOW)  # type: ignore[arg-type]
    assert result.roles == []
    assert result.hidden == 1


def test_format_fit_list_reports_hidden_roles() -> None:
    assert format_fit_list([], 1, hidden=2) == (
        "no new fitting roles in the last 1 day(s)\n"
        "hidden: 2 roles already in the application tracker"
    )
    job = _job()
    result = fit_roles(
        _Session([job], [_posting(job)], []),  # type: ignore[arg-type]
        now=NOW,
    )
    text = format_fit_list(result.roles, 1, now=NOW, hidden=result.hidden)
    assert text.endswith("\n\nhidden: 0 roles already in the application tracker")


def test_fit_roles_keeps_a_distinct_requisition_in_the_same_group() -> None:
    tracked = _job()
    twin = _job(url=OTHER_ASHBY)
    session = _Session(
        [tracked, twin],
        [_posting(tracked), _posting(twin)],
        [_stored(tracked.url, "Acme", "Senior Software Engineer")],
    )
    result = fit_roles(session, now=NOW)  # type: ignore[arg-type]
    assert [r.url for r in result.roles] == [OTHER_ASHBY]
    assert result.hidden == 1


def test_parse_tracker_rejects_a_row_without_a_leading_pipe() -> None:
    text = TRACKER.replace("| NoLink |", "NoLink |")
    with pytest.raises(TrackerError, match="start with"):
        parse_tracker(text)


def test_parse_tracker_keeps_parentheses_in_angle_links() -> None:
    text = (
        "| Company | Role | Link | Status |\n|---|---|---|---|\n"
        "| Acme | SWE | [Posting](<https://acme.test/jobs/engineer-(platform)>) | skipped |\n"
    )
    [row] = parse_tracker(text)
    assert row.url == "https://acme.test/jobs/engineer-(platform)"


class _SyncSession:
    def __init__(self, stored: list[tuple[str, str, str | None]]) -> None:
        self.stored = stored
        self.rolled_back = False
        self.added: list[Any] = []
        self.committed = False

    def execute(self, statement: object) -> _Result:
        if (
            "SELECT" in str(statement).upper()
            and "FIT_DECISIONS" in str(statement).upper()
        ):
            return _Result(
                [
                    SimpleNamespace(company_key=c, title_key=t, url_key=u)
                    for c, t, u in self.stored
                ]
            )
        return _Result([])

    def rollback(self) -> None:
        self.rolled_back = True

    def add(self, row: Any) -> None:  # noqa: ANN401
        self.added.append(row)

    def commit(self) -> None:
        self.committed = True


def test_sync_refuses_a_swap_that_drops_a_decision() -> None:
    a, b, c = parse_tracker(TRACKER)[:3]
    session = _SyncSession([_identity(a), _identity(b)])
    with pytest.raises(TrackerError, match="1 stored decision"):
        sync_decisions(session, [a, c])  # type: ignore[arg-type]
    assert session.rolled_back and not session.committed
    summary = sync_decisions(session, [a, c], allow_removals=True)  # type: ignore[arg-type]
    assert summary.removed == 1 and session.committed


def test_sync_accepts_a_tracker_that_only_grows() -> None:
    a, b, c = parse_tracker(TRACKER)[:3]
    session = _SyncSession([_identity(a), _identity(b)])
    summary = sync_decisions(session, [a, b, c])  # type: ignore[arg-type]
    assert summary == SyncSummary(total=3, removed=0)
    assert len(session.added) == 3


def test_fit_roles_matches_a_greenhouse_job_under_its_employer_url() -> None:
    job = _job(
        title="Senior Software Engineer",
        url="https://careers.acme.example/openings/123",
        provider="greenhouse",
        external_id="123",
    )
    session = _Session(
        [job],
        [_posting(job)],
        [
            _stored(
                "https://job-boards.greenhouse.io/acme/jobs/123", "Acme", "Senior SWE"
            )
        ],
    )
    result = fit_roles(session, now=NOW)  # type: ignore[arg-type]
    assert result.roles == []
    assert result.hidden == 1


def test_fit_roles_hides_aggregator_copies_without_a_first_party_row() -> None:
    himalayas = _job(source="himalayas", url=RAMP, company_name="Ramp")
    adzuna = _job(
        source="adzuna",
        company_name="Ramp",
        location="Remote - United States",
        url="https://www.adzuna.com/details/999",
    )
    session = _Session(
        [himalayas, adzuna],
        [],
        [_stored(RAMP, "Ramp", "Senior SWE, Product Foundations")],
    )
    result = fit_roles(session, now=NOW)  # type: ignore[arg-type]
    assert result.roles == []


def test_parse_tracker_keeps_balanced_parentheses_in_plain_links() -> None:
    text = (
        "| Company | Role | Link | Status |\n|---|---|---|---|\n"
        "| Acme | SWE | [Posting](https://example.test/jobs/engineer-(platform)) | skipped |\n"
    )
    [row] = parse_tracker(text)
    assert row.url == "https://example.test/jobs/engineer-(platform)"


def test_posting_keys_adds_the_provider_id() -> None:
    assert posting_keys(
        "https://careers.acme.example/openings/123", "greenhouse", "123"
    ) == {
        "careers.acme.example/openings/123",
        "greenhouse:123",
    }
    assert posting_keys("https://x.test/a", "himalayas", "abc") == {"x.test/a"}


GH_123 = "https://careers.acme.example/openings/123"


def test_employer_link_to_a_requisition_keeps_other_requisitions() -> None:
    tracked = _job(url=GH_123, provider="greenhouse", external_id="123")
    other = _job(
        url="https://careers.acme.example/openings/456",
        provider="greenhouse",
        external_id="456",
    )
    copy = _job(source="himalayas", url=GH_123, location="Remote - United States")
    session = _Session(
        [tracked, other, copy],
        [_posting(tracked), _posting(other)],
        [_stored(GH_123, "Acme", "Senior Software Engineer")],
    )
    result = fit_roles(session, now=NOW)  # type: ignore[arg-type]
    assert [r.url for r in result.roles] == [other.url]


def test_provider_id_found_through_a_link_covers_its_other_urls() -> None:
    tracked = _job(url=GH_123, provider="greenhouse", external_id="123")
    moved = _job(
        url="https://careers.acme.example/jobs/senior-engineer",
        provider="greenhouse",
        external_id="123",
        title="Senior Engineer",
    )
    session = _Session(
        [moved], [_posting(tracked)], [_stored(GH_123, "Acme", "Senior SWE")]
    )
    result = fit_roles(session, now=NOW)  # type: ignore[arg-type]
    assert result.roles == []


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        (
            "https://acme.example/job?jobId=123",
            "https://acme.example/job?jobId=456",
            False,
        ),
        (
            "https://acme.example/job?jobId=123&utm_source=x",
            "https://acme.example/job?jobId=123",
            True,
        ),
    ],
)
def test_url_key_keeps_identifying_query_params(
    first: str, second: str, same: bool
) -> None:
    assert (url_key(first) == url_key(second)) is same


def test_parse_tracker_keeps_parentheses_before_a_link_title() -> None:
    text = (
        "| Company | Role | Link | Status |\n|---|---|---|---|\n"
        '| Acme | SWE | [Posting](https://acme.example/jobs/engineer-(platform) "Acme Careers") | skipped |\n'
    )
    [row] = parse_tracker(text)
    assert row.url == "https://acme.example/jobs/engineer-(platform)"


@pytest.mark.parametrize("title", ["'Acme Careers'", "(Acme Careers)"])
def test_parse_tracker_accepts_other_link_title_forms(title: str) -> None:
    text = (
        "| Company | Role | Link | Status |\n|---|---|---|---|\n"
        f"| Acme | SWE | [Opening](https://acme.example/jobs/engineer-(platform) {title}) | skipped |\n"
    )
    [row] = parse_tracker(text)
    assert row.url == "https://acme.example/jobs/engineer-(platform)"


def test_url_key_keeps_query_order() -> None:
    assert url_key("https://a.example/j?jobId=123&jobId=456") != url_key(
        "https://a.example/j?jobId=456&jobId=123"
    )


def test_fit_roles_dates_an_aggregator_copy_by_the_company_posting() -> None:
    old = datetime(2026, 7, 30, tzinfo=UTC)
    company = _job(posted_at=old, first_seen=old)
    company_row = _posting(company)
    copy = _job(
        source="himalayas",
        url="https://himalayas.app/companies/acme/jobs/sse",
        posted_at=NOW,
        location="Remote - United States",
    )
    result = fit_roles(_Session([copy], [company_row], []), now=NOW)  # type: ignore[arg-type]
    assert result.roles == []
    fresh_row = SimpleNamespace(**{**vars(_posting(company)), "posted_at": NOW})
    result = fit_roles(_Session([copy], [fresh_row], []), now=NOW)  # type: ignore[arg-type]
    assert [r.official_url for r in result.roles] == [company.url]
