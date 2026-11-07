import httpx
import pytest

from app.jobtrends.hrsmart import (
    PROVIDER_HRSMART,
    HrsmartClient,
    HrsmartConfig,
    parse_hrsmart,
    parse_total,
)

CFG = HrsmartConfig(host="acme.hua.hrsmart.com")


def _row(posting_id: int, title: str, locations: list[str]) -> str:
    cells = "<br />".join(locations)
    return (
        f'<tr> <td><a href="/hr/ats/Posting/view/{posting_id}"> <span>{title}</span>'
        f"</a></td> <td> {cells}<br /> </td>"
        '<td class="actions_td"><a href="/ats/lens.php?action=find_similar_jobs'
        f'&amp;requisition={posting_id}">x</a></td> </tr>'
    )


def _page(rows: list[str], total: int | None, shown: int | None = None) -> str:
    count = (
        f"<div>Displaying 1 - {shown if shown is not None else len(rows)} of {total}</div>"
        if total is not None
        else ""
    )
    return (
        "<html><body><script>var Displaying = 1;</script>"
        f"{count}"
        '<table id="jobSearchResultsGrid_table" class="datatable"><thead class="header">'
        '<tr><th><a href="/hr/ats/JobSearch/viewAll/x">Job Title</a></th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></body></html>"
    )


def test_parse_rows_with_multi_location_and_entities() -> None:
    page = _page(
        [
            _row(
                1289,
                "Software Developer &amp; Data Engineer",
                ["Fort Collins, CO, US (Primary)", "Laramie, WY, US"],
            ),
            _row(1290, "Environmental Intern", ["Lakewood, CO, US 80401"]),
        ],
        2,
    )
    jobs = parse_hrsmart("acme", "Acme", CFG, page)
    assert [j.external_id for j in jobs] == ["1289", "1290"]
    first = jobs[0]
    assert first.provider == PROVIDER_HRSMART
    assert first.title == "Software Developer & Data Engineer"
    assert first.location == "Fort Collins, CO, US / Laramie, WY, US"
    assert first.url == "https://acme.hua.hrsmart.com/hr/ats/Posting/view/1289"


def test_parse_ignores_pages_without_the_results_table() -> None:
    assert parse_hrsmart("acme", "Acme", CFG, "<html>maintenance</html>") == []


def test_parse_total_reads_count_and_empty_board() -> None:
    assert parse_total(_page([], 34, shown=25)) == 34
    assert parse_total(_page([], 0)) == 0
    assert parse_total("<p>No jobs found</p>") is None
    assert parse_total("<html>login required</html>") is None


def _client(pages: dict[int, str]) -> HrsmartClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "@" not in request.headers["user-agent"]
        page = int(str(request.url).rsplit("jobSearchPaginationExternal_page:", 1)[1])
        return httpx.Response(200, text=pages.get(page, _page([], 0)))

    return HrsmartClient(transport=httpx.MockTransport(handler))


def test_client_paginates_until_total() -> None:
    first = [_row(i, f"Job {i}", ["Laramie, WY, US"]) for i in range(100)]
    second = [_row(100 + i, f"Job {100 + i}", ["Laramie, WY, US"]) for i in range(5)]
    jobs = _client({1: _page(first, 105), 2: _page(second, 105)}).fetch(
        "acme", "Acme", CFG
    )
    assert len(jobs) == 105


def test_client_accepts_a_genuinely_empty_board() -> None:
    assert _client({1: _page([], 0)}).fetch("acme", "Acme", CFG) == []


def test_client_raises_on_error_page_saying_no_records() -> None:
    page = "<h1>Job search unavailable</h1><p>No records found</p>"
    with pytest.raises(ValueError):
        _client({1: page}).fetch("acme", "Acme", CFG)


@pytest.mark.parametrize(
    "row",
    [
        '<tr><td><a href="/hr/ats/Posting/view/7"><span>Dev</span></a></td>'
        "<td> </td><td>x</td></tr>",
        '<tr><td><a href="/hr/ats/Posting/view/7"><span>Dev</span></a></td>'
        "<td>x</td></tr>",
        '<tr><td><a href="/hr/ats/Posting/view/7"></a></td>'
        "<td>Laramie, WY, US</td><td>x</td></tr>",
    ],
)
def test_parse_raises_on_malformed_posting_rows(row: str) -> None:
    with pytest.raises(ValueError):
        parse_hrsmart("acme", "Acme", CFG, _page([row], 1))


def test_client_raises_without_a_job_count() -> None:
    with pytest.raises(ValueError):
        _client({1: "<html>login required</html>"}).fetch("acme", "Acme", CFG)


def test_client_raises_when_rows_fall_short_of_total() -> None:
    rows = [_row(1, "Job", ["Laramie, WY, US"])]
    with pytest.raises(ValueError):
        _client({1: _page(rows, 34)}).fetch("acme", "Acme", CFG)


def test_client_raises_on_http_error() -> None:
    client = HrsmartClient(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch("acme", "Acme", CFG)
