"""NEOGOV / governmentjobs.com source — the local-government long tail.

State and local government is a big chunk of the *actually-local* job market
(State of Wyoming alone lists ~320 openings), and none of it is on the tech ATSs.
NEOGOV (governmentjobs.com) powers most of it, and its job list is an HTML fragment
behind one endpoint:

    GET https://www.governmentjobs.com/careers/home/index?agency={agency}
        &sort=PostingDate&isDescendingSort=true&page={n}
        (send X-Requested-With: XMLHttpRequest)

Each card carries a `/careers/{agency}/jobs/{id}` link (title), a salary range with a
period word (Monthly/Hourly/Biweekly/Annually), and Category/Department/Division
lines. We paginate, parse, annualize pay, and — because a gov board is overwhelmingly
NON-tech — keep only tech-relevant roles (the caller then location-filters via
`role_in_shed`). Reusable for any governmentjobs.com client (other states/cities).

`parse_neogov` is pure + unit-testable; the client is a thin httpx wrapper with an
injectable transport for tests.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass

import httpx

from app.config import settings
from app.jobtrends.ats import ParsedJob

logger = logging.getLogger(__name__)

PROVIDER_NEOGOV = "neogov"

_PAGE_SIZE = 20  # NEOGOV returns ~20 cards/page
_MAX_PAGES = 20

# A gov board is ~95% non-tech; keep only roles a software/data/IT person would want.
# Matched against the TITLE only — gov Category/Department strings are broad
# multi-value labels that let dispatchers/admins/drivers through.
_TECH_RE = re.compile(
    r"\b(software|developer|programmer|\bit\b|information technology|technolog(?:y|ist)|"
    r"\bdata\b|database|systems? (?:analyst|administrator|engineer|programmer|architect)|"
    r"network (?:engineer|administrator|analyst)|cyber|security engineer|\bgis\b|"
    r"web developer|application developer|devops|cloud engineer|"
    r"business intelligence|\betl\b)\b",
    re.IGNORECASE,
)

_PERIOD_MULT = {"monthly": 12, "hourly": 2080, "biweekly": 26, "annually": 1}


@dataclass(frozen=True)
class NeogovConfig:
    agency: str  # governmentjobs.com client slug, e.g. "wyoming"


def _annualize(lo: str, hi: str, period: str) -> tuple[int, int] | None:
    mult = _PERIOD_MULT.get(period.lower())
    if mult is None:
        return None
    try:
        a = int(round(float(lo.replace(",", "")) * mult))
        b = int(round(float(hi.replace(",", "")) * mult))
        return (a, b) if a > 0 else None
    except ValueError:
        return None


def _attr(block: str, name: str) -> str | None:
    m = re.search(rf'{name}="([^"]*)"', block)
    return html.unescape(m.group(1)).strip() if m else None


# "Cheyenne, WY Rural and Frontier Health Unit Manager 2026-01751" → (loc, title)
_TITLE_LOC = re.compile(r"^([A-Za-z .'&/-]+,\s*[A-Z]{2})\s+(.+)$")


def parse_neogov(
    cfg: NeogovConfig, fragment: str, *, tech_only: bool = True
) -> list[ParsedJob]:
    """NEOGOV careers HTML fragment -> ParsedJob[]. Pure. Deduped by job id.

    Cards are split on `data-job-id` (one per card; each card has several anchors,
    which is why an anchor split mis-associates fields). Title + location come from
    `data-title` ("City, ST Title"), department from `data-department-name`, salary
    and Category from the card's condensed text.
    """
    base = f"https://www.governmentjobs.com/careers/{cfg.agency}"
    jobs: list[ParsedJob] = []
    seen: set[str] = set()
    for c in re.split(r'(?=data-job-id=")', fragment):
        jid = _attr(c, "data-job-id")
        if not jid or jid in seen:
            continue
        seen.add(jid)
        # data-title = "City, ST  <title>"; split the location prefix off.
        dtitle = _attr(c, "data-title") or ""
        loc_m = _TITLE_LOC.match(dtitle)
        location = loc_m.group(1) if loc_m else None
        title = (loc_m.group(2) if loc_m else dtitle).strip()
        if not title:
            aria = re.search(r'aria-label="([^"]+?)(?: New Job Link| Job Link)', c)
            title = html.unescape(aria.group(1)).strip() if aria else ""
        if not title:
            continue
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c))
        cat_m = re.search(
            r"Category:\s*(.+?)\s*(?:Department:|Division:|Open\b|$)", text
        )
        category = cat_m.group(1).strip() if cat_m else None
        department = _attr(c, "data-department-name")
        if tech_only and not _TECH_RE.search(title):
            continue
        sal = re.search(
            r"\$([\d,]+(?:\.\d+)?)\s*-\s*\$([\d,]+(?:\.\d+)?)\s*"
            r"(Monthly|Hourly|Biweekly|Annually)",
            text,
            re.IGNORECASE,
        )
        comp: dict = {}
        if sal:
            annual = _annualize(sal.group(1), sal.group(2), sal.group(3))
            if annual:
                comp = {
                    "comp_min": annual[0],
                    "comp_max": annual[1],
                    "comp_currency": "USD",
                    "comp_period": "year",
                    "comp_kind": "structured",
                }
        jobs.append(
            ParsedJob(
                provider=PROVIDER_NEOGOV,
                company_token=cfg.agency,
                company_name=f"{cfg.agency.title()} (gov)",
                external_id=jid,
                title=title,
                location=location,
                department=department,
                url=_attr(c, "data-url") or f"{base}/jobs/{jid}",
                content_text=" · ".join(x for x in (title, category, department) if x),
                posted_at=None,
                **comp,
            )
        )
    return jobs


class NeogovClient:
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

    def fetch(self, cfg: NeogovConfig, *, tech_only: bool = True) -> list[ParsedJob]:
        """Paginate the NEOGOV listing (bounded) and parse. Stops on an empty page."""
        url = "https://www.governmentjobs.com/careers/home/index"
        headers = {
            "User-Agent": self._user_agent,
            "X-Requested-With": "XMLHttpRequest",
        }
        out: list[ParsedJob] = []
        with httpx.Client(
            timeout=self.timeout_seconds, transport=self._transport
        ) as client:
            for page in range(1, _MAX_PAGES + 1):
                params = {
                    "agency": cfg.agency,
                    "sort": "PostingDate",
                    "isDescendingSort": "true",
                    "page": str(page),
                }
                resp = client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                got = parse_neogov(cfg, resp.text, tech_only=tech_only)
                # No job links at all on the page → past the end.
                if f"/careers/{cfg.agency}/jobs/" not in resp.text:
                    break
                out.extend(got)
        # Dedup across pages (NEOGOV can repeat a card on the last page).
        by_id = {j.external_id: j for j in out}
        return list(by_id.values())
