from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings
from app.jobtrends.ats import ParsedJob

PROVIDER_ORACLE_HCM = "oracle_hcm"

_PAGE_SIZE = 50
_MAX_PAGES = 20


@dataclass(frozen=True)
class OracleHcmConfig:
    host: str
    site: str


def _posted_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_oracle_hcm(
    company_token: str,
    company_name: str,
    cfg: OracleHcmConfig,
    requisitions: list[dict[str, Any]],
) -> list[ParsedJob]:
    jobs: list[ParsedJob] = []
    for req in requisitions:
        external_id = str(req.get("Id") or "").strip()
        title = str(req.get("Title") or "").strip()
        if not external_id or not title:
            continue
        summary = str(req.get("ShortDescriptionStr") or "").strip()
        jobs.append(
            ParsedJob(
                provider=PROVIDER_ORACLE_HCM,
                company_token=company_token,
                company_name=company_name,
                external_id=external_id,
                title=title,
                location=req.get("PrimaryLocation") or None,
                department=req.get("Department") or None,
                url=(
                    f"https://{cfg.host}/hcmUI/CandidateExperience/en/sites/"
                    f"{cfg.site}/job/{external_id}"
                ),
                content_text=" · ".join(x for x in (title, summary) if x),
                posted_at=_posted_at(req.get("PostedDate")),
            )
        )
    return jobs


class OracleHcmClient:
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
        self, company_token: str, company_name: str, cfg: OracleHcmConfig
    ) -> list[ParsedJob]:
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        by_id: dict[str, ParsedJob] = {}
        expected_total = 0
        with httpx.Client(
            timeout=self.timeout_seconds, transport=self._transport
        ) as client:
            for page in range(_MAX_PAGES):
                offset = page * _PAGE_SIZE
                url = (
                    f"https://{cfg.host}/hcmRestApi/resources/latest/"
                    "recruitingCEJobRequisitions?onlyData=true"
                    "&expand=requisitionList&finder=findReqs;"
                    f"siteNumber={cfg.site},limit={_PAGE_SIZE},offset={offset},"
                    "sortBy=POSTING_DATES_DESC"
                )
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                items = resp.json().get("items")
                if not items:
                    raise ValueError(f"oracle hcm response has no items: {cfg.host}")
                requisitions = items[0].get("requisitionList")
                total = items[0].get("TotalJobsCount")
                if not isinstance(requisitions, list) or not isinstance(total, int):
                    raise ValueError(f"oracle hcm response is incomplete: {cfg.host}")
                if page == 0:
                    expected_total = total
                for job in parse_oracle_hcm(
                    company_token, company_name, cfg, requisitions
                ):
                    by_id[job.external_id] = job
                if not requisitions or offset + _PAGE_SIZE >= expected_total:
                    break
        if len(by_id) < expected_total:
            raise ValueError(
                f"oracle hcm returned {len(by_id)} of {expected_total} jobs: {cfg.host}"
            )
        return list(by_id.values())
