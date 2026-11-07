from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from app.config import settings
from app.jobtrends.ats import ParsedJob

PROVIDER_HIRINGTHING = "hiringthing"

_JOB_ID_RE = re.compile(r"/job/(\d+)(?:/|$)")


@dataclass(frozen=True)
class HiringThingConfig:
    host: str


def _child_text(item: ET.Element, tag: str) -> str:
    return (item.findtext(tag) or "").strip()


def _plain(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(raw))).strip()


def parse_hiringthing_rss(
    company_token: str, company_name: str, cfg: HiringThingConfig, feed: str
) -> list[ParsedJob]:
    try:
        root = ET.fromstring(feed)
    except ET.ParseError as exc:
        raise ValueError(f"hiringthing feed is not valid XML: {cfg.host}") from exc
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError(f"hiringthing feed has no rss channel: {cfg.host}")
    jobs: list[ParsedJob] = []
    for item in channel.findall("item"):
        title = _child_text(item, "title")
        link = _child_text(item, "link")
        match = _JOB_ID_RE.search(link)
        if not title or not match:
            raise ValueError(f"hiringthing feed has a malformed item: {cfg.host}")
        summary = _plain(_child_text(item, "description"))
        jobs.append(
            ParsedJob(
                provider=PROVIDER_HIRINGTHING,
                company_token=company_token,
                company_name=company_name,
                external_id=match.group(1),
                title=title,
                location=_child_text(item, "location") or None,
                department=_child_text(item, "category") or None,
                url=link,
                content_text=" · ".join(x for x in (title, summary) if x),
                posted_at=None,
            )
        )
    return jobs


class HiringThingClient:
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
        self, company_token: str, company_name: str, cfg: HiringThingConfig
    ) -> list[ParsedJob]:
        headers = {"User-Agent": self._user_agent, "Accept": "application/rss+xml"}
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self._transport,
            follow_redirects=True,
        ) as client:
            resp = client.get(f"https://{cfg.host}/api/rss.xml", headers=headers)
            resp.raise_for_status()
            return parse_hiringthing_rss(company_token, company_name, cfg, resp.text)
