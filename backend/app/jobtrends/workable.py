from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.config import settings
from app.jobtrends.ats import ParsedJob

PROVIDER_WORKABLE = "workable"

_MAX_PAGES = 20


@dataclass(frozen=True)
class WorkableConfig:
    account: str


def _location(item: dict[str, Any], account: str) -> str | None:
    raw = item.get("location")
    if raw is None:
        place = ""
    elif isinstance(raw, str):
        place = raw.strip()
    elif isinstance(raw, dict):
        parts = [raw.get(k) for k in ("city", "region", "country")]
        if any(p is not None and not isinstance(p, str) for p in parts):
            raise ValueError(f"workable account {account} has a malformed location")
        place = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    else:
        raise ValueError(f"workable account {account} has a malformed location")
    if item.get("remote") or item.get("workplace") == "remote":
        return f"Remote ({place})" if place else "Remote"
    return place or None


def _posted_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_workable(
    company_token: str, company_name: str, cfg: WorkableConfig, results: Any
) -> list[ParsedJob]:
    if not isinstance(results, list):
        raise ValueError(f"workable account {cfg.account} has no results list")
    jobs: list[ParsedJob] = []
    for item in results:
        if not isinstance(item, dict):
            raise ValueError(f"workable account {cfg.account} has a malformed job")
        shortcode = str(item.get("shortcode") or "").strip()
        title = str(item.get("title") or "").strip()
        if not shortcode or not title:
            raise ValueError(
                f"workable account {cfg.account} has a job without id or title"
            )
        departments = item.get("department")
        department = (
            ", ".join(str(d) for d in departments if d)
            if isinstance(departments, list)
            else None
        )
        jobs.append(
            ParsedJob(
                provider=PROVIDER_WORKABLE,
                company_token=company_token,
                company_name=company_name,
                external_id=shortcode,
                title=title,
                location=_location(item, cfg.account),
                department=department or None,
                url=f"https://apply.workable.com/{cfg.account}/j/{shortcode}/",
                content_text=title,
                posted_at=_posted_at(item.get("published")),
            )
        )
    return jobs


class WorkableClient:
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
        self, company_token: str, company_name: str, cfg: WorkableConfig
    ) -> list[ParsedJob]:
        url = f"https://apply.workable.com/api/v3/accounts/{cfg.account}/jobs"
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        by_id: dict[str, ParsedJob] = {}
        expected_total = 0
        body: dict[str, Any] = {}
        with httpx.Client(
            timeout=self.timeout_seconds, transport=self._transport
        ) as client:
            for page in range(_MAX_PAGES):
                resp = client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict):
                    raise ValueError(f"workable account {cfg.account} bad response")
                if page == 0:
                    total = payload.get("total")
                    if type(total) is not int or total < 0:
                        raise ValueError(
                            f"workable account {cfg.account} has no valid total"
                        )
                    expected_total = total
                for job in parse_workable(
                    company_token, company_name, cfg, payload.get("results")
                ):
                    by_id[job.external_id] = job
                next_page = payload.get("nextPage")
                if not next_page or len(by_id) >= expected_total:
                    break
                body = {"token": next_page}
        if len(by_id) < expected_total:
            raise ValueError(
                f"workable returned {len(by_id)} of {expected_total} jobs: {cfg.account}"
            )
        return list(by_id.values())
