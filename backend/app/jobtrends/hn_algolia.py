"""Thin HN Algolia API client.

Two endpoints are all ingestion needs:
  - search_by_date, filtered to the `whoishiring` author, returns the monthly
    stories newest-first.
  - items/{id} returns the full comment tree; top-level children are job posts.

Notes from probing the live API (2026-07):
  - Use HTTPS. `http://hn.algolia.com` 301-redirects with an empty body.
  - `whoishiring` also posts "Who wants to be hired?" every month; the free-text
    query mostly excludes them, but callers should still title-guard (see ingest).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class HNAlgoliaClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.jobtrends_hn_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._user_agent = user_agent or settings.jobtrends_user_agent

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"User-Agent": self._user_agent}
        with httpx.Client(
            timeout=self.timeout_seconds, transport=self._transport
        ) as client:
            resp = client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def search_hiring_stories(self, limit: int) -> list[dict[str, Any]]:
        """Monthly 'Who is hiring?' stories, newest first (search_by_date order)."""
        data = self._get(
            "search_by_date",
            {
                "query": "Ask HN: Who is hiring?",
                "tags": "story,author_whoishiring",
                "hitsPerPage": limit,
            },
        )
        return list(data.get("hits") or [])

    def fetch_item(self, item_id: int | str) -> dict[str, Any]:
        """Full item incl. its comment tree (`children` = top-level comments)."""
        return self._get(f"items/{item_id}")
