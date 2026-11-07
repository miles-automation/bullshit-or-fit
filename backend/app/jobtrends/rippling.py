from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.jobtrends.ats import ParsedJob

PROVIDER_RIPPLING = "rippling"


@dataclass(frozen=True)
class RipplingConfig:
    board: str


def _label(value: Any) -> str | None:
    if isinstance(value, dict):
        label = value.get("label")
        return str(label).strip() or None if label else None
    return None


def parse_rippling(
    company_token: str, company_name: str, cfg: RipplingConfig, payload: Any
) -> list[ParsedJob]:
    if not isinstance(payload, list):
        raise ValueError(f"rippling board {cfg.board} did not return a list")
    jobs: list[ParsedJob] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"rippling board {cfg.board} has a malformed job")
        job_id = str(item.get("uuid") or "").strip()
        title = str(item.get("name") or "").strip()
        if not job_id or not title:
            raise ValueError(
                f"rippling board {cfg.board} has a job without id or title"
            )
        jobs.append(
            ParsedJob(
                provider=PROVIDER_RIPPLING,
                company_token=company_token,
                company_name=company_name,
                external_id=job_id,
                title=title,
                location=_label(item.get("workLocation")),
                department=_label(item.get("department")),
                url=item.get("url")
                or f"https://ats.rippling.com/{cfg.board}/jobs/{job_id}",
                content_text=title,
                posted_at=None,
            )
        )
    return jobs


class RipplingClient:
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
        self, company_token: str, company_name: str, cfg: RipplingConfig
    ) -> list[ParsedJob]:
        url = f"https://api.rippling.com/platform/api/ats/v1/board/{cfg.board}/jobs"
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        with httpx.Client(
            timeout=self.timeout_seconds, transport=self._transport
        ) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return parse_rippling(company_token, company_name, cfg, resp.json())
