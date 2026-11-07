from __future__ import annotations

import html
import re
from dataclasses import dataclass

import httpx

from app.config import settings
from app.jobtrends.ats import ParsedJob

PROVIDER_HRSMART = "hrsmart"

_PAGE_SIZE = 100
_MAX_PAGES = 10

_ROW_RE = re.compile(r"(?s)<tr[^>]*>(.*?)</tr>")
_CELL_RE = re.compile(r"(?s)<td[^>]*>(.*?)</td>")
_POSTING_RE = re.compile(r'href="/hr/ats/Posting/view/(\d+)"')
_TOTAL_RE = re.compile(r"Displaying\s+\d+\s*-\s*\d+\s+of\s+(\d+)")
_NO_RESULTS_RE = re.compile(
    r"No (?:jobs|results|records) (?:were )?found", re.IGNORECASE
)
_TABLE_ID = 'id="jobSearchResultsGrid_table"'


@dataclass(frozen=True)
class HrsmartConfig:
    host: str


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


def _locations(cell: str) -> str | None:
    parts = [
        re.sub(r"\s*\(Primary\)\s*$", "", _text(p))
        for p in re.split(r"<br\s*/?>", cell)
    ]
    joined = " / ".join(p for p in parts if p)
    return joined or None


def parse_total(page: str) -> int | None:
    plain = _text(re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", page))
    match = _TOTAL_RE.search(plain)
    if match:
        return int(match.group(1))
    if _NO_RESULTS_RE.search(plain):
        return 0
    return None


def parse_hrsmart(
    company_token: str, company_name: str, cfg: HrsmartConfig, page: str
) -> list[ParsedJob]:
    start = page.find(_TABLE_ID)
    if start < 0:
        return []
    end = page.find("</table>", start)
    table = page[start : end if end > 0 else len(page)]
    jobs: list[ParsedJob] = []
    for row in _ROW_RE.findall(table):
        posting = _POSTING_RE.search(row)
        cells = _CELL_RE.findall(row)
        if not posting or len(cells) < 2:
            continue
        title = _text(cells[0])
        if not title:
            continue
        posting_id = posting.group(1)
        jobs.append(
            ParsedJob(
                provider=PROVIDER_HRSMART,
                company_token=company_token,
                company_name=company_name,
                external_id=posting_id,
                title=title,
                location=_locations(cells[1]),
                department=None,
                url=f"https://{cfg.host}/hr/ats/Posting/view/{posting_id}",
                content_text=title,
                posted_at=None,
            )
        )
    return jobs


class HrsmartClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._user_agent = user_agent or settings.jobtrends_user_agent

    def fetch(
        self, company_token: str, company_name: str, cfg: HrsmartConfig
    ) -> list[ParsedJob]:
        headers = {"User-Agent": self._user_agent, "Accept": "text/html"}
        by_id: dict[str, ParsedJob] = {}
        expected_total = 0
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self._transport,
            follow_redirects=True,
        ) as client:
            for page in range(1, _MAX_PAGES + 1):
                url = (
                    f"https://{cfg.host}/hr/ats/JobSearch/viewAll/"
                    f"jobSearchPaginationExternal_pageSize:{_PAGE_SIZE}/"
                    f"jobSearchPaginationExternal_page:{page}"
                )
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                if page == 1:
                    total = parse_total(resp.text)
                    if total is None:
                        raise ValueError(f"hrsmart page has no job count: {cfg.host}")
                    expected_total = total
                rows = parse_hrsmart(company_token, company_name, cfg, resp.text)
                for job in rows:
                    by_id[job.external_id] = job
                if not rows or page * _PAGE_SIZE >= expected_total:
                    break
        if len(by_id) < expected_total:
            raise ValueError(
                f"hrsmart returned {len(by_id)} of {expected_total} jobs: {cfg.host}"
            )
        return list(by_id.values())
