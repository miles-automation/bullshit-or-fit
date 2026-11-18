from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.jobtrends.decisions import parse_tracker, resolve_rows
from app.jobtrends.fit import (
    DecidedIndex,
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


def test_resolve_rows_uses_the_stored_posting_title() -> None:
    rows = parse_tracker(TRACKER)
    jobs = [
        (
            "Ramp",
            "Senior Software Engineer | Product Foundations",
            "https://jobs.ashbyhq.com/ramp/a9cb5fd3-1acb-474e-ab7c-b811eeb5b0c2",
        ),
        ("Other", "Engineer", None),
    ]
    resolved = resolve_rows(rows, jobs)
    assert resolved[0][1:] == (
        "Ramp",
        "Senior Software Engineer | Product Foundations",
        True,
    )
    assert resolved[1][1:] == ("Reddit", "Senior SWE, DevX", False)


def _index(*rows: tuple[str, str, str | None]) -> DecidedIndex:
    keys = {url_key(url) for _, _, url in rows}
    return DecidedIndex(
        url_keys=frozenset(k for k in keys if k),
        roles=frozenset((normalize_company(c), normalize_title(t)) for c, t, _ in rows),
    )


def test_decided_index_matches_by_link() -> None:
    index = _index(
        (
            "Ramp",
            "Senior SWE",
            "https://jobs.ashbyhq.com/ramp/a9cb5fd3-1acb-474e-ab7c-b811eeb5b0c2",
        )
    )
    assert index.covers(
        "Ramp Business Corp",
        "Senior Software Engineer, Product Foundations",
        "https://jobs.ashbyhq.com/ramp/a9cb5fd3-1acb-474e-ab7c-b811eeb5b0c2/application",
    )


def test_decided_index_matches_same_role_from_another_source() -> None:
    index = _index(
        (
            "DataGrail",
            "Staff Software Engineer",
            "https://jobs.lever.co/datagrail/0b5e2a1c-1111-2222-3333-444455556666",
        )
    )
    assert index.covers(
        "DataGrail, Inc.",
        "Staff Software Engineer",
        "https://himalayas.app/companies/datagrail/jobs/staff-software-engineer",
    )


def test_decided_index_keeps_a_different_level_at_the_same_company() -> None:
    index = _index(("Twilio", "Staff Software Engineer", "https://x.test/1"))
    assert not index.covers("Twilio", "Senior Software Engineer", "https://x.test/2")
    assert not index.covers("Twilio", "Principal Software Engineer", None)


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
        team_size=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _decision(url: str | None, company: str, title: str) -> SimpleNamespace:
    return SimpleNamespace(
        url_key=url_key(url),
        company_key=normalize_company(company),
        title_key=normalize_title(title),
    )


def test_fit_roles_hides_decided_roles_and_their_copies() -> None:
    ats = _job()
    copy = _job(
        source="himalayas",
        url="https://himalayas.app/companies/acme/jobs/senior-software-engineer",
    )
    other = _job(
        title="Staff Software Engineer",
        url="https://jobs.ashbyhq.com/acme/0b5e2a1c-1111-2222-3333-444455556666",
    )
    session = _Session(
        [ats, copy, other],
        [("Acme", "Senior Software Engineer"), ("Acme", "Staff Software Engineer")],
        [_decision(ats.url, "Acme", "Senior SWE")],
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
        [("Acme", "Senior Software Engineer")],
        [_decision(ats.url, "Ramp", "Unrelated title")],
    )
    result = fit_roles(session, now=NOW)  # type: ignore[arg-type]
    assert result.roles == []
    assert result.hidden == 1


def test_format_fit_list_reports_hidden_roles() -> None:
    assert format_fit_list([], 1, hidden=2) == (
        "no new fitting roles in the last 1 day(s)\n"
        "hidden: 2 roles already applied to or skipped"
    )
    result = fit_roles(
        _Session([_job()], [("Acme", "Senior Software Engineer")], []),  # type: ignore[arg-type]
        now=NOW,
    )
    text = format_fit_list(result.roles, 1, now=NOW, hidden=1)
    assert text.endswith("\n\nhidden: 1 role already applied to or skipped")
