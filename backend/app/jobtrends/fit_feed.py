from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://sparkswarm.com"
BODY_LIMIT = 7500
PAGE_SIZE = 500


class FeedError(RuntimeError):
    pass


def _client(api_key: str | None, base_url: str | None) -> httpx.Client:
    key = api_key or os.environ.get("SPARK_SWARM_API_KEY")
    if not key:
        raise FeedError("SPARK_SWARM_API_KEY is not set")
    return httpx.Client(
        base_url=base_url or os.environ.get("SPARK_SWARM_URL", DEFAULT_BASE_URL),
        headers={"X-API-Key": key},
        timeout=30,
    )


def summary_line(document: dict[str, Any]) -> str:
    roles = document.get("roles") or document.get("shortlist") or []
    head = (
        f"{document.get('schema')} generated {document.get('generated_at')}: "
        f"{len(roles)} role(s)"
    )
    lines = [head]
    for role in roles[:3]:
        lines.append(
            f"- {role.get('company_name')}: {role.get('title')} "
            f"({role.get('official_url') or role.get('listing_url') or 'no link'})"
        )
    return "\n".join(lines)[:BODY_LIMIT]


def publish(
    document: dict[str, Any],
    task_id: int,
    *,
    author: str,
    api_key: str | None = None,
    base_url: str | None = None,
    client: httpx.Client | None = None,
) -> int:
    http = client or _client(api_key, base_url)
    response = http.post(
        f"/api/v1/tasks/{task_id}/comments",
        json={
            "body": summary_line(document),
            "author": author,
            "author_type": "agent",
            "comment_metadata": {**document, "kind": document.get("schema")},
        },
    )
    if response.status_code != 201:
        raise FeedError(f"publish failed: HTTP {response.status_code}")
    return int(response.json()["id"])


def latest(
    task_id: int,
    schema: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    http = client or _client(api_key, base_url)
    matches: list[dict[str, Any]] = []
    after: int | None = None
    while True:
        params: dict[str, Any] = {"limit": PAGE_SIZE, "kinds": schema}
        if after is not None:
            params["after_comment_id"] = after
        response = http.get(f"/api/v1/tasks/{task_id}/comments", params=params)
        if response.status_code != 200:
            raise FeedError(f"read failed: HTTP {response.status_code}")
        page = response.json().get("comments", [])
        matches.extend(
            c for c in page if (c.get("comment_metadata") or {}).get("schema") == schema
        )
        if len(page) < PAGE_SIZE:
            break
        after = max(int(c["id"]) for c in page)
    if not matches:
        raise FeedError(f"no {schema} document on task {task_id}")
    newest = max(matches, key=lambda c: (c["created_at"], c["id"]))
    return {
        "comment_id": newest["id"],
        "posted_at": newest["created_at"],
        "posted_by": newest.get("author"),
        "document": {
            k: v for k, v in newest["comment_metadata"].items() if k != "kind"
        },
    }
