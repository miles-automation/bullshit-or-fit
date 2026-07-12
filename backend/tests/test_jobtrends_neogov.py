"""Unit tests for the NEOGOV / governmentjobs.com source (pure parse + client)."""

import httpx

from app.jobtrends.neogov import (
    PROVIDER_NEOGOV,
    NeogovClient,
    NeogovConfig,
    parse_neogov,
)

CFG = NeogovConfig(agency="wyoming")


def _card(jid: str, city_title: str, dept: str, salary: str, category: str) -> str:
    # Mirrors the real NEOGOV markup: one data-job-id per card, title+location in
    # data-title ("City, ST Title"), department in an attr, salary+category in text.
    slug = jid
    return f"""
    <div class="list-item" data-job-id="{jid}">
      <h3 class="job-item-link-container">
        <a class="item-details-link" data-department-name="{dept}"
           data-url="https://www.governmentjobs.com/careers/wyoming/jobs/{slug}/x"
           data-title="{city_title}" href="/careers/wyoming/jobs/{jid}?p=1">
           <span>{city_title}</span></a>
      </h3>
      <span>Permanent Full-time - {salary}</span>
      <span>Category: {category} Department: {dept} Division: Cheyenne Open</span>
    </div>"""


# One tech card (kept) + one non-tech card (filtered out).
FRAGMENT = _card(
    "5406778",
    "Cheyenne, WY Medicaid Data &amp; Reporting Analyst 2026-01752",
    "Department Of Health",
    "$7,805.20 - $8,671.86 Monthly",
    "Information Technology / Data",
) + _card(
    "5405079",
    "Rawlins, WY Correctional Officer I",
    "Corrections",
    "$4,000.00 - $4,500.00 Monthly",
    "Public Safety",
)


def test_parse_keeps_tech_splits_location_and_annualizes() -> None:
    jobs = parse_neogov(CFG, FRAGMENT)
    assert len(jobs) == 1  # Correctional Officer filtered out (non-tech)
    j = jobs[0]
    assert j.provider == PROVIDER_NEOGOV and j.company_token == "wyoming"
    assert j.external_id == "5406778"
    assert (
        j.title == "Medicaid Data & Reporting Analyst 2026-01752"
    )  # location stripped, &amp; decoded
    assert j.location == "Cheyenne, WY"
    assert j.url == "https://www.governmentjobs.com/careers/wyoming/jobs/5406778/x"
    # $7,805.20/mo × 12 → ~$93.7k .. $104k, structured
    assert j.comp_currency == "USD" and j.comp_kind == "structured"
    assert 93_000 <= (j.comp_min or 0) <= 94_000
    assert 104_000 <= (j.comp_max or 0) <= 105_000


def test_tech_only_false_keeps_all() -> None:
    assert len(parse_neogov(CFG, FRAGMENT, tech_only=False)) == 2


def test_hourly_annualizes_x2080() -> None:
    frag = _card(
        "9",
        "Cheyenne, WY IT Systems Engineer",
        "ETS",
        "$40.00 - $50.00 Hourly",
        "Information Technology",
    )
    j = parse_neogov(CFG, frag)[0]
    assert j.comp_min == 83_200 and j.comp_max == 104_000  # 40×2080, 50×2080


def test_client_paginates_and_dedupes() -> None:
    page = _card(
        "1",
        "Cheyenne, WY Software Developer",
        "ETS",
        "$6,000.00 - $7,000.00 Monthly",
        "Information Technology",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        p = int(dict(request.url.params).get("page", "1"))
        assert dict(request.url.params)["agency"] == "wyoming"
        # pages 1 & 2 return the SAME job (dedupe); page 3+ empty → stop.
        return httpx.Response(200, text=page if p <= 2 else "<div>no jobs</div>")

    jobs = NeogovClient(transport=httpx.MockTransport(handler)).fetch(CFG)
    assert len(jobs) == 1 and jobs[0].external_id == "1"
